// Testy zakładki „Raporty zmian” i szczegółów raportu.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/auth/screens/subscription_screen.dart';
import 'package:tonten/features/changes/screens/change_report_screen.dart';
import 'package:tonten/features/changes/screens/change_reports_screen.dart';
import 'package:tonten/features/products/screens/product_details_screen.dart';

void main() {
  tearDown(ApiClient.resetClient);

  void desktopWindow(WidgetTester tester) {
    tester.view.physicalSize = const Size(1920, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
  }

  http.Response json(Object body, [int status = 200]) =>
      http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json; charset=utf-8'});

  Widget app() => const MaterialApp(home: Scaffold(body: ChangeReportsScreen()));

  final reports = [
    {
      'id': 2, 'created_at': '2026-11-09T09:20:00+00:00', 'unchecked_stores': ['calavado'],
      'price_changes': 2, 'new_offers': 1, 'removed_offers': 0,
    },
    {
      'id': 1, 'created_at': '2026-11-08T09:20:00+00:00', 'unchecked_stores': [],
      'price_changes': 0, 'new_offers': 0, 'removed_offers': 0,
    },
  ];

  final items = [
    {
      'type': 'price', 'changed_by': 'competitor', 'client_sku': 'A', 'client_name': 'koc wełniany', 'size': '150x200',
      'store': 'pod_pierzyna', 'competitor_name': 'koc wełniany natur', 'product_id': 42,
      'old_competitor_price': 120.0, 'new_competitor_price': 110.0, 'old_client_price': 100.0, 'new_client_price': 100.0,
      'old_difference': 20.0, 'new_difference': 10.0,
    },
    {
      'type': 'removed', 'client_sku': 'C', 'client_name': 'pled', 'size': '130x170', 'store': 'calavado',
      'competitor_name': 'pled', 'product_id': null, 'old_competitor_price': 310.0,
    },
  ];

  void mockApi({List<Object>? reportList, List<Object>? reportItems, bool locked = false}) {
    ApiClient.setClient(MockClient((request) async {
      final path = request.url.path;
      if (path == '/api/change-reports') {
        return locked
            ? json({'ok': false, 'locked': true, 'subscription_plan': 'Podstawowy'}, 403)
            : json({'ok': true, 'data': reportList ?? []});
      }
      if (path.startsWith('/api/change-reports/')) {
        return json({'ok': true, 'data': {'id': 2, 'unchecked_stores': [], 'items': reportItems ?? []}});
      }
      if (path == '/api/me') return json({'ok': true, 'user': {'subscription_plan': 'Pro'}});
      if (path == '/api/products/42') {
        return json({'ok': true, 'data': {'id': 42, 'name': 'koc wełniany', 'sku': 'A'}, 'competitors': []});
      }
      return json({'ok': true, 'data': []});
    }));
  }

  test('odmiana liczebników', () {
    expect(plural(1, 'zmiana ceny', 'zmiany cen', 'zmian cen'), '1 zmiana ceny');
    expect(plural(3, 'zmiana ceny', 'zmiany cen', 'zmian cen'), '3 zmiany cen');
    expect(plural(12, 'zmiana ceny', 'zmiany cen', 'zmian cen'), '12 zmian cen');
    expect(plural(22, 'zmiana ceny', 'zmiany cen', 'zmian cen'), '22 zmiany cen');
  });

  testWidgets('bez pakietu Pro zakładka jest widoczna z opisem i przejściem do wyboru pakietu', (tester) async {
    desktopWindow(tester);
    mockApi(locked: true);

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();

    expect(find.text('Raporty zmian'), findsOneWidget);
    await tester.tap(find.byKey(const Key('change_plan_reports')));
    await tester.pumpAndSettle();

    expect(find.byType(SubscriptionScreen), findsOneWidget);
  });

  testWidgets('brak raportów', (tester) async {
    desktopWindow(tester);
    mockApi();

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();

    expect(find.textContaining('Pierwszy raport pojawi się'), findsOneWidget);
  });

  testWidgets('kafelki raportów z datą aktualizacji i podsumowaniem', (tester) async {
    desktopWindow(tester);
    mockApi(reportList: reports);

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();

    expect(find.text('2 zmiany cen · 1 nowa oferta'), findsOneWidget);
    expect(find.text('Nie sprawdzono sklepów: calavado'), findsOneWidget);
    expect(find.text(noChangesText), findsOneWidget);
    expect(find.textContaining('Aktualizacja '), findsNWidgets(2));
  });

  testWidgets('raport bez zmian', (tester) async {
    desktopWindow(tester);
    mockApi(reportList: reports, reportItems: []);

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('change_report_1')));
    await tester.pumpAndSettle();

    expect(find.byType(ChangeReportScreen), findsOneWidget);
    expect(find.text(noChangesText), findsOneWidget);
  });

  testWidgets('zmiany w grupach, pozycja otwiera stronę produktu', (tester) async {
    desktopWindow(tester);
    mockApi(reportList: reports, reportItems: items);

    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('change_report_2')));
    await tester.pumpAndSettle();

    expect(find.text('Zmiany cen (1)'), findsOneWidget);
    expect(find.text('Wycofane oferty (1)'), findsOneWidget);
    expect(find.text('Nowe oferty konkurencji (1)'), findsNothing);
    expect(find.text('Cena konkurencji: 120.00 zł → 110.00 zł'), findsOneWidget);
    expect(find.text('Różnica: +20.00 zł → +10.00 zł'), findsOneWidget);
    expect(find.text('Twoja cena: 100.00 zł → 100.00 zł'), findsNothing);
    expect(find.text('Produktu nie ma już w Twoim sklepie.'), findsOneWidget);

    await tester.tap(find.byKey(const Key('change_item_price_A_pod_pierzyna')));
    await tester.pumpAndSettle();

    expect(find.byType(ProductDetailsScreen), findsOneWidget);
  });
}
