import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:tonten/features/auth/screens/login_screen.dart';

void main() {
  setUpAll(() {
    GoogleFonts.config.allowRuntimeFetching = false;
  });

  Future<void> pumpLogin(WidgetTester tester) async {
    await tester.pumpWidget(const MaterialApp(home: LoginScreen()));
    await tester.pumpAndSettle();
  }

  group('LoginScreen – walidacja formularza', () {
    testWidgets('pokazuje błąd przy niepoprawnym e-mailu', (tester) async {
      await pumpLogin(tester);

      await tester.enterText(
        find.widgetWithText(TextField, 'E-mail'),
        'nieprawidlowy-email',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Hasło'),
        'haslo123',
      );
      await tester.pump();

      final loginButton = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Zaloguj się'),
      );
      loginButton.onPressed!();
      await tester.pump();

      expect(find.text('Podaj poprawny adres e-mail'), findsOneWidget);
    });

    testWidgets('przycisk logowania jest nieaktywny, gdy pola są puste',
        (tester) async {
      await pumpLogin(tester);

      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNull);
    });

    testWidgets('przycisk logowania aktywuje się po wypełnieniu obu pól',
        (tester) async {
      await pumpLogin(tester);

      await tester.enterText(
        find.widgetWithText(TextField, 'E-mail'),
        'user@test.pl',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Hasło'),
        'haslo123',
      );
      await tester.pump();

      final button = tester.widget<FilledButton>(find.byType(FilledButton));
      expect(button.onPressed, isNotNull);
    });
  });

  group('LoginScreen – reakcja na błędne dane uwierzytelniające', () {
    testWidgets('pokazuje komunikat z API, gdy serwer zwróci 401',
        (tester) async {
      final mockClient = MockClient((request) async {
        expect(request.url.path, '/api/login');
        return http.Response(
          jsonEncode({
            'ok': false,
            'error': 'Nieprawidłowy login lub hasło.',
          }),
          401,
          headers: {'content-type': 'application/json'},
        );
      });

      await http.runWithClient(() async {
        await pumpLogin(tester);

        await tester.enterText(
          find.widgetWithText(TextField, 'E-mail'),
          'user@test.pl',
        );
        await tester.enterText(
          find.widgetWithText(TextField, 'Hasło'),
          'zlehaslo',
        );
        await tester.pump();

        await tester.tap(find.widgetWithText(FilledButton, 'Zaloguj się'));
        
        await tester.pump(); 
        
        await tester.pump(Duration.zero); 
        
        await tester.pump(); 

        expect(
          find.text('Nieprawidłowy login lub hasło.'),
          findsOneWidget,
        );
      }, () => mockClient);
    });

    testWidgets('pokazuje komunikat o błędzie połączenia, gdy serwer nie odpowiada',
        (tester) async {
      final mockClient = MockClient((request) async {
        throw Exception('Connection refused');
      });

      await http.runWithClient(() async {
        await pumpLogin(tester);

        await tester.enterText(
          find.widgetWithText(TextField, 'E-mail'),
          'user@test.pl',
        );
        await tester.enterText(
          find.widgetWithText(TextField, 'Hasło'),
          'haslo123',
        );
        await tester.pump();

        // 1. Zmieniamy na tester.tap
        await tester.tap(find.widgetWithText(FilledButton, 'Zaloguj się'));
        
        // 2. Powtarzamy ten sam schemat oczekiwania (start, odpowiedź, render)
        await tester.pump();
        await tester.pump(Duration.zero);
        await tester.pump();

        expect(find.textContaining('Błąd komunikacji z serwerem'), findsOneWidget);
      }, () => mockClient);
    });
  });
}