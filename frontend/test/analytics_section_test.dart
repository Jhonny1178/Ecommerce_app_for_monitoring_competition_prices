// Testy sekcji statystyk Dashboardu (AnalyticsSection) i przejścia do podejrzanych dopasowań.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/dashboard/screens/dashboard_screen.dart';
import 'package:tonten/features/dashboard/widgets/analytics_section.dart';
import 'package:tonten/features/products/screens/product_details_screen.dart';

void main() {
  tearDown(ApiClient.resetClient);

  http.Response json(Object body) =>
      http.Response(jsonEncode(body), 200, headers: {'content-type': 'application/json; charset=utf-8'});

  Map<String, dynamic> dashboardData({int risky = 20}) => {
        'ok': true,
        'risk_threshold_pct': 50,
        'summary': {
          'total_products': 13962,
          'matched_products': 1775,
          'stores': 2,
          'offers': 2168,
          'median_diff': -3.1,
          'risky_offers': risky,
          'risky_products': 16,
        },
        'position': {'you_cheapest': 227, 'competitor_cheaper': 1304, 'same_price': 235},
        'stores': [
          {'store': 'Pod Pierzyna', 'offers': 1145, 'cheaper': 995, 'median_diff': -11.1},
          {'store': 'Calavado', 'offers': 1023, 'cheaper': 498, 'median_diff': 0.0},
        ],
        'categories': [
          {'name': 'Ręczniki', 'products': 840, 'competitor_cheaper': 700, 'median_diff': -3.0},
        ],
        'brands': [
          {'name': 'graccioza', 'products': 667, 'competitor_cheaper': 567, 'median_diff': -11.1},
        ],
        'biggest_gaps': {
          'competitor_cheaper': [
            {'product_id': 7, 'name': 'Kołdra Gold Down', 'sku': 'K-1', 'store': 'Calavado', 'ours': 2795.0, 'theirs': 1766.07, 'diff': -1028.93},
          ],
          'you_cheaper': [],
        },
      };

  void wideWindow(WidgetTester tester) {
    tester.view.physicalSize = const Size(1600, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
  }

  Future<int> pumpSection(WidgetTester tester, Map<String, dynamic> data) async {
    wideWindow(tester);
    var riskyTaps = 0;
    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/dashboard') return json(data);
      return json({'ok': true});
    }));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: AnalyticsSection(onShowRiskyMatches: () => riskyTaps++),
        ),
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    return riskyTaps;
  }

  testWidgets('pokazuje pokrycie, typową różnicę (medianę) i sklepy', (tester) async {
    await pumpSection(tester, dashboardData());

    expect(find.text('1775 z 13962'), findsOneWidget);
    expect(find.text('taniej o 3.10 zł'), findsOneWidget);
    expect(find.text('Pod Pierzyna'), findsOneWidget);
    expect(find.text('995 (87%)'), findsOneWidget);
    expect(find.text('ta sama cena'), findsOneWidget, reason: 'Calavado: mediana 0 zł');
    expect(find.text('1304 (74%)'), findsOneWidget);
  });

  testWidgets('komunikat o pominiętych dopasowaniach jest klikalny', (tester) async {
    wideWindow(tester);
    var taps = 0;
    ApiClient.setClient(MockClient((request) async => json(dashboardData())));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SingleChildScrollView(child: AnalyticsSection(onShowRiskyMatches: () => taps++))),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.textContaining('Pominięto 20 podejrzanych dopasowań w 16 wariantach (różnica ceny ponad 50%)'), findsOneWidget);
    // Komunikat jest na samym dole, pod listami największych różnic.
    expect(
      tester.getTopLeft(find.byKey(const Key('risky_matches_note'))).dy,
      greaterThan(tester.getBottomLeft(find.byKey(const Key('gaps_competitor_cheaper'))).dy),
    );

    await tester.ensureVisible(find.byKey(const Key('risky_matches_note')));
    await tester.tap(find.byKey(const Key('risky_matches_note')));
    expect(taps, 1);
  });

  testWidgets('bez podejrzanych dopasowań nie ma komunikatu', (tester) async {
    await pumpSection(tester, dashboardData(risky: 0));
    expect(find.byKey(const Key('risky_matches_note')), findsNothing);
  });

  testWidgets('przełącznik pokazuje marki zamiast kategorii', (tester) async {
    await pumpSection(tester, dashboardData());
    expect(find.text('Ręczniki'), findsOneWidget);

    await tester.tap(find.text('Marki'));
    await tester.pump();
    expect(find.text('graccioza'), findsOneWidget);
    expect(find.text('Ręczniki'), findsNothing);
  });

  testWidgets('największe różnice prowadzą do produktu, pusta lista ma opis', (tester) async {
    await pumpSection(tester, dashboardData());
    expect(find.text('1028.93 zł taniej'), findsOneWidget);
    expect(find.descendant(of: find.byKey(const Key('gaps_you_cheaper')), matching: find.text('Brak takich ofert.')), findsOneWidget);

    await tester.tap(find.text('Kołdra Gold Down'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.byType(ProductDetailsScreen), findsOneWidget);
  });

  testWidgets('bez dopasowań (albo bez danych) jest opis zamiast pustych tabel', (tester) async {
    await pumpSection(tester, {'ok': true});
    expect(find.byKey(const Key('analytics_empty')), findsOneWidget);
    expect(find.byKey(const Key('stores_panel')), findsNothing);
  });

  testWidgets('Dashboard: komunikat otwiera "Zmatchowane produkty" z filtrem podejrzanych', (tester) async {
    wideWindow(tester);
    final requested = <Uri>[];
    ApiClient.setClient(MockClient((request) async {
      switch (request.url.path) {
        case '/api/me':
          return json({'ok': true, 'user': {'status': 'active'}});
        case '/api/dashboard':
          return json(dashboardData());
        case '/api/filters':
          return json({'ok': true, 'categories': [], 'brands': [], 'stores': []});
        case '/api/products':
          requested.add(request.url);
          return json({'ok': true, 'data': [], 'pagination': {'total_pages': 1, 'total_items': 0}});
      }
      return json({'ok': true});
    }));

    await tester.pumpWidget(const MaterialApp(home: DashboardScreen()));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    await tester.ensureVisible(find.byKey(const Key('risky_matches_note')));
    await tester.tap(find.byKey(const Key('risky_matches_note')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(requested.last.queryParameters['matched_only'], 'true');
    expect(requested.last.queryParameters['risk'], 'high');
    expect(find.text('Podejrzane dopasowania (różnica ponad 50%)'), findsOneWidget);

    // Ręczne wejście w zakładkę to zwykła lista, bez filtra z Dashboardu.
    await tester.tap(find.text('Dashboard'));
    await tester.pump();
    await tester.tap(find.text('Zmatchowane produkty'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(requested.last.queryParameters.containsKey('risk'), isFalse);
  });
}
