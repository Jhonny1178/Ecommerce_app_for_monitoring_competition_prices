// Testy zaznaczania i kopiowania tekstu na ekranach aplikacji (withSelectableText).
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tonten/core/widgets/selectable_text_theme.dart';

void main() {
  late List<String> clipboard;
  final desktop = TargetPlatformVariant.only(TargetPlatform.macOS);

  setUp(() {
    clipboard = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        if (call.method == 'Clipboard.setData') {
          clipboard.add((call.arguments as Map)['text'] as String);
        }
        return null;
      },
    );
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null);
  });

  Widget app({ThemeMode mode = ThemeMode.light}) => MaterialApp(
        themeMode: mode,
        theme: withSelectableText(ThemeData.light()),
        darkTheme: withSelectableText(ThemeData.dark()),
        home: Builder(
          builder: (context) => Scaffold(
            body: Column(children: [
              const Text('SKU-12345'),
              TextButton(
                // Center: na macOS lewe 20 px ekranu to gest "przesuń, aby wrócić".
                onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => const Scaffold(body: Center(child: Text('Szczegóły produktu'))),
                )),
                child: const Text('Otwórz'),
              ),
            ]),
          ),
        ),
      );

  // Zaznacza cały tekst myszką i kopiuje skrótem Cmd+C.
  Future<void> selectAndCopy(WidgetTester tester, Finder finder) async {
    final rect = tester.getRect(finder);
    final gesture = await tester.startGesture(rect.centerLeft + const Offset(1, 0), kind: PointerDeviceKind.mouse);
    await tester.pump();
    await gesture.moveTo(rect.centerRight + const Offset(10, 0));
    await tester.pump();
    await gesture.up();
    await tester.pumpAndSettle();

    await tester.sendKeyDownEvent(LogicalKeyboardKey.metaLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyC);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.metaLeft);
    await tester.pumpAndSettle();
  }

  testWidgets('zaznaczony tekst kopiuje się skrótem Cmd+C', (tester) async {
    await tester.pumpWidget(app());
    await selectAndCopy(tester, find.text('SKU-12345'));

    expect(tester.takeException(), isNull);
    expect(clipboard.last, 'SKU-12345');
  }, variant: desktop);

  testWidgets('prawy przycisk pokazuje menu z kopiowaniem', (tester) async {
    await tester.pumpWidget(app());
    final gesture = await tester.startGesture(
      tester.getCenter(find.text('SKU-12345')),
      kind: PointerDeviceKind.mouse,
      buttons: kSecondaryMouseButton,
    );
    await gesture.up();
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('Copy'), findsOneWidget);
  }, variant: desktop);

  testWidgets('kopiowanie działa także na ekranie otwartym z Navigatora', (tester) async {
    await tester.pumpWidget(app());
    await tester.tap(find.text('Otwórz'));
    await tester.pumpAndSettle();

    await selectAndCopy(tester, find.text('Szczegóły produktu'));
    expect(clipboard.last, 'Szczegóły produktu');
  }, variant: desktop);

  testWidgets('Cmd+A zaznacza tylko bieżący ekran, bez ukrytego ekranu pod spodem', (tester) async {
    await tester.pumpWidget(app());
    await tester.tap(find.text('Otwórz'));
    await tester.pumpAndSettle();
    await selectAndCopy(tester, find.text('Szczegóły produktu'));

    await tester.sendKeyDownEvent(LogicalKeyboardKey.metaLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyA);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyC);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.metaLeft);
    await tester.pumpAndSettle();

    expect(clipboard.last, 'Szczegóły produktu');
  }, variant: desktop);

  testWidgets('po zmianie motywu kopiowanie nadal działa', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpWidget(app(mode: ThemeMode.dark));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);

    await selectAndCopy(tester, find.text('SKU-12345'));
    expect(clipboard.last, 'SKU-12345');
  }, variant: desktop);
}
