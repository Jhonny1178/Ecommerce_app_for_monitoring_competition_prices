import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/products/screens/product_details_screen.dart';
import 'package:tonten/features/products/screens/product_variants_screen.dart';
import 'package:tonten/features/products/screens/products_list_screen.dart';
import 'package:tonten/features/products/widgets/product_tiles.dart';

void main() {
  tearDown(() {
    // Żeby jeden test nie "wyciekał" swojego mocka do kolejnego.
    ApiClient.resetClient();
  });

  Future<void> pumpProductsList(WidgetTester tester, {bool matchedOnly = false}) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProductsListScreen(matchedOnly: matchedOnly),
        ),
      ),
    );
  }

  http.Response productsResponse(List<Map<String, dynamic>> products) => http.Response(
        jsonEncode({
          'ok': true,
          'data': products,
          'pagination': {'total_pages': 1},
        }),
        200,
        headers: {'content-type': 'application/json; charset=utf-8'},
      );

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

  group('filtr różnicy ceny w dopasowanych produktach', () {
    testWidgets('pokazuje ceny konkurencji i różnicę przy produkcie', (tester) async {
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        return productsResponse([
          {
            'id': 1,
            'name': 'Ręcznik vegan life',
            'sku': 'SKU-1',
            'display_price': 25.0,
            'competitors_count': 2,
            'competitor_prices': [
              {'store': 'calavado', 'price': 189.0, 'difference': 164.0, 'difference_percent': 656.0},
              {'store': 'pod_pierzyna', 'price': 20.5, 'difference': -4.5, 'difference_percent': -18.0},
            ],
          },
        ]);
      }));

      await pumpProductsList(tester);
      await tester.pumpAndSettle();

      expect(find.text('calavado: 189.00 zł (+164.00 zł, +656.0%)'), findsOneWidget);
      expect(find.text('pod_pierzyna: 20.50 zł (-4.50 zł, -18.0%)'), findsOneWidget);
    });

    testWidgets('wysyła kierunek i wielkość różnicy do API', (tester) async {
      final requested = <Uri>[];
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        requested.add(request.url);
        return productsResponse([]);
      }));

      await pumpProductsList(tester, matchedOnly: true);
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('price_diff_dropdown')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Konkurencja tańsza').last);
      await tester.pumpAndSettle();

      expect(requested.last.queryParameters['price_diff'], 'lower');
      expect(requested.last.queryParameters['group'], 'true');
      expect(requested.last.queryParameters.containsKey('diff_min'), isFalse);

      await tester.enterText(find.byKey(const Key('diff_min_field')), '12,5');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();

      expect(requested.last.queryParameters['price_diff'], 'lower');
      expect(requested.last.queryParameters['diff_min'], '12.5');
      expect(requested.last.queryParameters['diff_unit'], 'pln');
    });

    testWidgets('na liście wszystkich produktów filtra nie ma', (tester) async {
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        return productsResponse([]);
      }));

      await pumpProductsList(tester);
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('price_diff_dropdown')), findsNothing);
    });
  });

  group('kafelki produktów w zakładce dopasowanych', () {
    Map<String, dynamic> tile({int matching = 2, int total = 5, int firstId = 7}) => {
          'group_name': 'ręcznik vegan life',
          'group_maker': 'vossen',
          'name': 'ręcznik vegan life',
          'manufacturer': 'vossen',
          'category': 'Ręczniki',
          'first_id': firstId,
          'variants_matching': matching,
          'variants_total': total,
          'min_price': 25.0,
          'max_price': 159.0,
          'min_diff_pct': -4.0,
          'max_diff_pct': 12.5,
        };

    testWidgets('jeden kafelek na produkt z liczbą wariantów', (tester) async {
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        return productsResponse([tile(matching: 5, total: 5)]);
      }));

      await pumpProductsList(tester, matchedOnly: true);
      await tester.pumpAndSettle();

      expect(find.text('ręcznik vegan life'), findsOneWidget);
      expect(find.text('5 wariantów'), findsOneWidget);
      expect(find.text('25.00 – 159.00 zł'), findsOneWidget);
      expect(find.text('Konkurencja: od -4.0% do +12.5%'), findsOneWidget);
    });

    testWidgets('przy filtrze kafelek mówi, ile wariantów go spełnia, a klik otwiera warianty', (tester) async {
      final requested = <Uri>[];
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        requested.add(request.url);
        if (request.url.queryParameters.containsKey('group_name')) {
          return productsResponse([
            {'id': 11, 'name': 'ręcznik vegan life', 'sku': '4852-A', 'size': '30x30', 'display_price': 25.0},
            {'id': 12, 'name': 'ręcznik vegan life', 'sku': '4852-B', 'size': '50x100', 'display_price': 59.0},
          ]);
        }
        return productsResponse([tile()]);
      }));

      await pumpProductsList(tester, matchedOnly: true);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('price_diff_dropdown')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Konkurencja tańsza').last);
      await tester.pumpAndSettle();

      expect(find.text('Filtry spełnia: 2 z 5 wariantów'), findsOneWidget);

      await tester.tap(find.text('ręcznik vegan life'));
      await tester.pumpAndSettle();

      expect(find.byType(ProductVariantsScreen), findsOneWidget);
      final variantsRequest = requested.last.queryParameters;
      expect(variantsRequest['group_name'], 'ręcznik vegan life');
      expect(variantsRequest['group_maker'], 'vossen');
      expect(variantsRequest['price_diff'], 'lower');
      expect(variantsRequest.containsKey('group'), isFalse);
      expect(find.textContaining('Rozmiar: 50x100'), findsOneWidget);

      await tester.tap(find.byKey(const Key('show_all_variants')));
      await tester.pumpAndSettle();
      expect(requested.last.queryParameters.containsKey('price_diff'), isFalse);
    });

    testWidgets('kafelek z jednym wariantem otwiera od razu szczegóły', (tester) async {
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        if (request.url.path == '/api/products') return productsResponse([tile(matching: 1, total: 1, firstId: 42)]);
        return http.Response(jsonEncode({'ok': false}), 404, headers: {'content-type': 'application/json'});
      }));

      await pumpProductsList(tester, matchedOnly: true);
      await tester.pumpAndSettle();
      await tester.tap(find.text('ręcznik vegan life'));
      await tester.pumpAndSettle();

      expect(find.byType(ProductDetailsScreen), findsOneWidget);
      expect(find.byType(ProductVariantsScreen), findsNothing);
    });

    testWidgets('sortowanie po największej różnicy wysyła parametry do API', (tester) async {
      final requested = <Uri>[];
      ApiClient.setClient(MockClient((request) async {
        if (request.url.path == '/api/filters') return filtersResponse();
        requested.add(request.url);
        return productsResponse([]);
      }));

      await pumpProductsList(tester, matchedOnly: true);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('sort_dropdown')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Największa różnica (zł)').last);
      await tester.pumpAndSettle();

      expect(requested.last.queryParameters['sort'], 'diff_pln');
      expect(requested.last.queryParameters['order'], 'desc');
    });
  });

  test('odmiana słowa "wariant"', () {
    expect(variantsLabel(1), '1 wariant');
    expect(variantsLabel(3), '3 warianty');
    expect(variantsLabel(5), '5 wariantów');
    expect(variantsLabel(12), '12 wariantów');
    expect(variantsLabel(22), '22 warianty');
  });
}
