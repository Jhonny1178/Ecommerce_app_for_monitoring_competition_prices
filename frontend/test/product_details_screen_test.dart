import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/auth/screens/subscription_screen.dart';
import 'package:tonten/features/products/screens/product_details_screen.dart';

void main() {
  tearDown(ApiClient.resetClient);

  void desktopWindow(WidgetTester tester) {
    tester.view.physicalSize = const Size(1920, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
  }

  http.Response json(Object body) =>
      http.Response(jsonEncode(body), 200, headers: {'content-type': 'application/json; charset=utf-8'});

  testWidgets('szczegóły pokazują wariant i link do produktu w sklepie klienta', (tester) async {
    desktopWindow(tester);
    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/me') return json({'ok': true, 'user': {'subscription_plan': 'Basic'}});
      return json({
        'ok': true,
        'data': {
          'id': 42,
          'name': 'pościel żakardowa muriel 2406-4',
          'sku': '15133-R',
          'size': '155x200',
          'color': 'zielony',
          'display_price': 708.0,
          'url': 'https://lambfield.com/product-pol-15133-Muriel.feed10000.html',
        },
        'competitors': [],
      });
    }));

    await tester.pumpWidget(const MaterialApp(home: ProductDetailsScreen(productId: 42)));
    await tester.pumpAndSettle();

    expect(find.text('SKU: 15133-R | Rozmiar: 155x200 | Kolor: zielony'), findsOneWidget);
    expect(find.byKey(const Key('client_product_link')), findsOneWidget);
  });

  testWidgets('bez adresu produktu nie ma linku', (tester) async {
    desktopWindow(tester);
    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/me') return json({'ok': true, 'user': {'subscription_plan': 'Basic'}});
      return json({'ok': true, 'data': {'id': 1, 'name': 'produkt', 'sku': 'X'}, 'competitors': []});
    }));

    await tester.pumpWidget(const MaterialApp(home: ProductDetailsScreen(productId: 1)));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('client_product_link')), findsNothing);
  });

  test('adres z feedu zamieniany na stronę produktu', () {
    expect(
      clientProductUrl('https://lambfield.com/product-pol-15133-Muriel.feed10000.html'),
      'https://lambfield.com/product-pol-15133-Muriel.html',
    );
    expect(clientProductUrl('https://sklep.pl/produkt.html'), 'https://sklep.pl/produkt.html');
    expect(clientProductUrl(null), '');
  });

  group('pakiety', () {
    Map<String, dynamic> product = {
      'id': 5, 'name': 'produkt', 'sku': 'S-1', 'display_price': 10.0, 'url': 'https://sklep.pl/p.html',
    };

    void mockApi(String plan) {
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/me') return json({'ok': true, 'user': {'subscription_plan': plan}});
        return json({'ok': true, 'data': product, 'competitors': []});
      }));
    }

    testWidgets('niższy pakiet: "Zmień pakiet" przy wycenie otwiera stronę pakietów', (tester) async {
      desktopWindow(tester);
      mockApi('Podstawowy');

      await tester.pumpWidget(const MaterialApp(home: ProductDetailsScreen(productId: 5)));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('change_plan_recommendation')));
      await tester.pumpAndSettle();

      expect(find.byType(SubscriptionScreen), findsOneWidget);
      expect(find.text('Wybierz nowy pakiet'), findsOneWidget);
    });

    testWidgets('pakiet Premium nie ma przycisku zmiany pakietu przy wycenie', (tester) async {
      desktopWindow(tester);
      mockApi('Premium');

      await tester.pumpWidget(const MaterialApp(home: ProductDetailsScreen(productId: 5)));
      await tester.pumpAndSettle();

      expect(find.text('Inteligentna wycena'), findsOneWidget);
      expect(find.byKey(const Key('change_plan_recommendation')), findsNothing);
    });

    testWidgets('z zakładki "Wszystkie produkty" nie ma inteligentnej wyceny', (tester) async {
      desktopWindow(tester);
      mockApi('Premium');

      await tester.pumpWidget(const MaterialApp(home: ProductDetailsScreen(productId: 5, showPriceRecommendation: false)));
      await tester.pumpAndSettle();

      expect(find.text('Inteligentna wycena'), findsNothing);
    });
  });
}
