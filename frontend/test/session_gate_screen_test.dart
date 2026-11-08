// Testy przywracania sesji na starcie aplikacji (SessionGateScreen).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/features/auth/screens/landing_page_screen.dart';
import 'package:tonten/features/auth/screens/login_screen.dart';
import 'package:tonten/features/auth/screens/pending_approval_screen.dart';
import 'package:tonten/features/auth/screens/session_gate_screen.dart';

void main() {
  tearDown(ApiClient.resetClient);

  Future<void> pumpGate(WidgetTester tester, MockClientHandler handler) async {
    tester.view.physicalSize = const Size(1920, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    ApiClient.setClient(MockClient(handler));
    await tester.pumpWidget(MaterialApp(
      navigatorKey: ApiClient.navigatorKey,
      home: const SessionGateScreen(),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
  }

  testWidgets('ważna sesja: od razu ekran użytkownika zamiast strony powitalnej', (tester) async {
    await pumpGate(tester, (request) async {
      expect(request.url.path, '/api/me');
      return http.Response(
        jsonEncode({'ok': true, 'user': {'status': 'pending_admin', 'is_admin': false}}),
        200,
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    });

    expect(find.byType(PendingApprovalScreen), findsOneWidget);
    expect(find.byType(LandingPageScreen), findsNothing);
  });

  // Czcionka testowa (Ahem) jest szersza od prawdziwej, więc karty cen na stronie powitalnej "przepełniają się" tylko w testach.
  void Function() ignoreOverflowErrors() {
    final original = FlutterError.onError;
    FlutterError.onError = (details) {
      if (details.exceptionAsString().contains('overflowed')) return;
      original?.call(details);
    };
    return () => FlutterError.onError = original;
  }

  testWidgets('brak sesji (401): strona powitalna bez komunikatu o wygaśnięciu', (tester) async {
    final restore = ignoreOverflowErrors();
    await pumpGate(tester, (request) async {
      return http.Response(jsonEncode({'ok': false, 'error': 'Not authenticated'}), 401);
    });
    await tester.pump(const Duration(milliseconds: 500));

    expect(find.byType(LandingPageScreen), findsOneWidget);
    expect(find.byType(LoginScreen), findsNothing);
    expect(find.text('Sesja wygasła. Zaloguj się ponownie.'), findsNothing);
    restore();
  });

  testWidgets('błąd sieci: strona powitalna', (tester) async {
    final restore = ignoreOverflowErrors();
    await pumpGate(tester, (request) async {
      throw http.ClientException('brak połączenia');
    });

    expect(find.byType(LandingPageScreen), findsOneWidget);
    restore();
  });
}
