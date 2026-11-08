// Testy panelu "Zgłoś błąd" na widoku produktu (ReportIssueSheet).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/products/widgets/report_issue_sheet.dart';

void main() {
  tearDown(ApiClient.resetClient);

  const offers = [
    ReportOffer(store: 'Calavado', sku: 'C-1', name: 'Kołdra C', url: 'https://calavado.example/k', price: 519),
    ReportOffer(store: 'Pod Pierzyna', sku: 'P-1', name: 'Kołdra P', price: 499),
  ];

  late List<Map<String, dynamic>> sent;

  void mockApi({int status = 200, Map<String, dynamic> response = const {'ok': true}}) {
    sent = [];
    ApiClient.setClient(MockClient((request) async {
      sent.add({'path': request.url.path, 'body': jsonDecode(request.body)});
      return http.Response(jsonEncode(response), status, headers: {'content-type': 'application/json; charset=utf-8'});
    }));
  }

  Future<void> openSheet(WidgetTester tester, {ReportTopic? topic, int? offerIndex, ReportRecommendation? recommendation}) async {
    // Wysokie okno: czcionka testowa (Ahem) jest szeroka, więc znaczniki zajmują dużo wierszy.
    tester.view.physicalSize = const Size(1400, 2000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () => showReportIssueSheet(
              context,
              productId: 2903,
              productName: 'Kołdra Hefel',
              offers: offers,
              recommendation: recommendation,
              topic: topic,
              offerIndex: offerIndex,
            ),
            child: const Text('otwórz'),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('otwórz'));
    await tester.pumpAndSettle();
  }

  FilledButton sendButton(WidgetTester tester) =>
      tester.widget<FilledButton>(find.byKey(const Key('report_send')));

  testWidgets('wysłać można dopiero po wyborze tematu i powodu albo opisu', (tester) async {
    mockApi();
    await openSheet(tester);
    expect(sendButton(tester).onPressed, isNull);

    await tester.tap(find.byKey(const Key('report_topic_product')));
    await tester.pumpAndSettle();
    expect(sendButton(tester).onPressed, isNull);

    await tester.tap(find.byKey(const Key('report_reason_wrong_own_price')));
    await tester.pumpAndSettle();
    expect(sendButton(tester).onPressed, isNotNull);
  });

  testWidgets('zgłoszenie oferty wysyła temat, powody, opis i dane oferty', (tester) async {
    mockApi();
    await openSheet(tester, topic: ReportTopic.comparison, offerIndex: 1);

    expect(tester.widget<ChoiceChip>(find.byKey(const Key('report_offer_1'))).selected, isTrue);

    await tester.tap(find.byKey(const Key('report_reason_wrong_match')));
    await tester.tap(find.byKey(const Key('report_reason_wrong_variant')));
    await tester.enterText(find.byKey(const Key('report_comment')), 'U nich to wersja 200x220');
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('report_send')));
    await tester.pumpAndSettle();

    expect(sent.single['path'], '/api/products/2903/report');
    final body = sent.single['body'] as Map;
    expect(body['topic'], 'comparison');
    expect(body['reasons'], ['wrong_match', 'wrong_variant']);
    expect(body['comment'], 'U nich to wersja 200x220');
    expect(body['offer']['store'], 'Pod Pierzyna');
    expect(body['offer']['sku'], 'P-1');
    expect(body.containsKey('recommendation'), isFalse);

    expect(find.byType(ReportIssueSheet), findsNothing);
    expect(find.text('Dziękujemy! Zgłoszenie trafiło do administratora.'), findsOneWidget);
  });

  testWidgets('zmiana tematu czyści wybrane powody', (tester) async {
    mockApi();
    await openSheet(tester, topic: ReportTopic.comparison);
    await tester.tap(find.byKey(const Key('report_reason_wrong_match')));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('report_topic_recommendation')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('report_reason_wrong_match')), findsNothing);
    expect(sendButton(tester).onPressed, isNull);
  });

  testWidgets('zgłoszenie rekomendacji dołącza sugerowaną cenę i uzasadnienie', (tester) async {
    mockApi();
    await openSheet(
      tester,
      topic: ReportTopic.recommendation,
      recommendation: const ReportRecommendation(price: 819, reason: 'Obniż cenę z 825 zł do 819 zł.'),
    );
    await tester.tap(find.byKey(const Key('report_reason_reason_mismatch')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('report_send')));
    await tester.pumpAndSettle();

    final body = sent.single['body'] as Map;
    expect(body['topic'], 'recommendation');
    expect(body['recommendation'], {'price': 819, 'reason': 'Obniż cenę z 825 zł do 819 zł.'});
    expect(body.containsKey('offer'), isFalse);
  });

  testWidgets('"Inne" wymaga własnego opisu', (tester) async {
    mockApi();
    await openSheet(tester, topic: ReportTopic.other);
    expect(find.textContaining('Co jest nie tak?'), findsNothing);
    expect(sendButton(tester).onPressed, isNull);

    await tester.enterText(find.byKey(const Key('report_comment')), 'Zdjęcie się nie ładuje');
    await tester.pumpAndSettle();
    expect(sendButton(tester).onPressed, isNotNull);
  });

  testWidgets('błąd serwera zostawia panel otwarty i pokazuje komunikat', (tester) async {
    mockApi(status: 400, response: {'ok': false, 'error': 'Wybierz powód albo opisz problem.'});
    await openSheet(tester, topic: ReportTopic.product);
    await tester.tap(find.byKey(const Key('report_reason_wrong_name')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('report_send')));
    await tester.pumpAndSettle();

    expect(find.byType(ReportIssueSheet), findsOneWidget);
    expect(find.text('Wybierz powód albo opisz problem.'), findsOneWidget);
  });
}
