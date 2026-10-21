import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/products/screens/products_list_screen.dart';

void main() {
  tearDown(() {
    // Żeby jeden test nie "wyciekał" swojego mocka do kolejnego.
    ApiClient.resetClient();
  });

  Future<void> pumpProductsList(WidgetTester tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: ProductsListScreen(matchedOnly: false),
        ),
      ),
    );
  }

  http.Response filtersResponse() => http.Response(
        jsonEncode({
          'ok': true,
          'categories': [],
          'brands': [],
          'stores': [],
          'max_stores': 5,
        }),
        200,
        headers: {'content-type': 'application/json'},
      );

  testWidgets('pokazuje wskaźnik ładowania, dopóki odpowiedź nie nadejdzie',
      (tester) async {
    final productsCompleter = Completer<http.Response>();

    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/filters') return filtersResponse();
      return productsCompleter.future;
    }));

    await pumpProductsList(tester);
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    productsCompleter.complete(http.Response(
      jsonEncode({
        'ok': true,
        'data': [],
        'pagination': {'total_pages': 1},
      }),
      200,
      headers: {'content-type': 'application/json'},
    ));
    await tester.pumpAndSettle();

    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('renderuje listę produktów po poprawnej odpowiedzi API',
      (tester) async {
    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/filters') return filtersResponse();

      return http.Response(
        jsonEncode({
          'ok': true,
          'data': [
            {
              'id': 1,
              'name': 'Produkt testowy',
              'sku': 'SKU-001',
              'price_special': 99.99,
              'competitors_count': 2,
            },
          ],
          'pagination': {'total_pages': 1},
        }),
        200,
        headers: {'content-type': 'application/json'},
      );
    }));

    await pumpProductsList(tester);
    await tester.pumpAndSettle();

    expect(find.text('Produkt testowy'), findsOneWidget);
    expect(find.textContaining('SKU-001'), findsOneWidget);
    expect(find.text('Brak produktów spełniających kryteria.'), findsNothing);
  });

  testWidgets('pokazuje stan pusty/błędu, gdy serwer zwróci błąd sieciowy',
      (tester) async {
    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/filters') return filtersResponse();
      throw Exception('Connection refused');
    }));

    await pumpProductsList(tester);
    await tester.pumpAndSettle();

    
    expect(find.text('Brak produktów spełniających kryteria.'), findsOneWidget);
  });

  testWidgets('pokazuje komunikat błędu przez SnackBar, gdy API zwróci kod != 200',
      (tester) async {
    ApiClient.setClient(MockClient((request) async {
      if (request.url.path == '/api/filters') return filtersResponse();

      return http.Response(
        jsonEncode({'ok': false, 'error': 'Błąd serwera'}),
        500,
        headers: {'content-type': 'application/json'},
      );
    }));

    await pumpProductsList(tester);
    await tester.pumpAndSettle();

    expect(find.textContaining('Błąd pobierania'), findsOneWidget);
  });
}