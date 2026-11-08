// Testy paska „Są nowe dane” na dashboardzie po zakończeniu przebiegu pipeline'u.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/dashboard/screens/dashboard_screen.dart';

void main() {
  tearDown(ApiClient.resetClient);

  const bannerText = 'Są nowe dane: zakończyła się aktualizacja cen.';
  const interval = Duration(minutes: 2);

  http.Response json(Object body) =>
      http.Response(jsonEncode(body), 200, headers: {'content-type': 'application/json; charset=utf-8'});

  late String? lastUpdate;
  late int versionRequests;

  Future<void> pumpDashboard(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1920, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    lastUpdate = '2026-11-08T08:00:00';
    versionRequests = 0;
    ApiClient.setClient(MockClient((request) async {
      switch (request.url.path) {
        case '/api/me':
          return json({'ok': true, 'user': {'status': 'active'}});
        case '/api/stats':
          return json({'ok': true, 'summary': {}});
        case '/api/data_version':
          versionRequests++;
          return json({'ok': true, 'last_update': lastUpdate});
      }
      return json({'ok': true});
    }));

    await tester.pumpWidget(const MaterialApp(home: DashboardScreen()));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
  }

  testWidgets('bez nowego przebiegu paska nie ma', (tester) async {
    await pumpDashboard(tester);
    await tester.pump(interval);

    expect(versionRequests, 2);
    expect(find.text(bannerText), findsNothing);
  });

  testWidgets('po nowym przebiegu pojawia się pasek, a "Odśwież dane" go chowa', (tester) async {
    await pumpDashboard(tester);

    lastUpdate = '2026-11-08T12:40:00';
    await tester.pump(interval);
    await tester.pump();
    expect(find.text(bannerText), findsOneWidget);

    await tester.tap(find.text('Odśwież dane'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text(bannerText), findsNothing);

    // Nowa wersja przyjęta jako widziana: kolejne sprawdzenie nie pokazuje paska ponownie.
    await tester.pump(interval);
    await tester.pump();
    expect(find.text(bannerText), findsNothing);
  });

  testWidgets('ukrycie paska przyjmuje wersję, pasek wraca dopiero po kolejnym przebiegu', (tester) async {
    await pumpDashboard(tester);

    lastUpdate = '2026-11-08T12:40:00';
    await tester.pump(interval);
    await tester.pump();
    await tester.tap(find.byTooltip('Ukryj'));
    await tester.pump();
    expect(find.text(bannerText), findsNothing);

    await tester.pump(interval);
    await tester.pump();
    expect(find.text(bannerText), findsNothing);

    lastUpdate = '2026-11-09T12:40:00';
    await tester.pump(interval);
    await tester.pump();
    expect(find.text(bannerText), findsOneWidget);
  });
}
