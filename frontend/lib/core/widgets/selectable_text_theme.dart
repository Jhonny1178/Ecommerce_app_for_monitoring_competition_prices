// Pozwala zaznaczać i kopiować tekst na każdym ekranie aplikacji (Flutter web domyślnie na to nie pozwala).
import 'package:flutter/material.dart';

// Każdy ekran (MaterialPageRoute) dostaje własną SelectionArea w swoim zakresie fokusu,
// dzięki czemu Cmd+C działa także na ekranach otwartych przez Navigator. Animacje przejść zostają bez zmian.
ThemeData withSelectableText(ThemeData theme) {
  final original = theme.pageTransitionsTheme;

  return theme.copyWith(
    pageTransitionsTheme: PageTransitionsTheme(builders: {
      for (final platform in TargetPlatform.values) platform: _SelectablePageTransitionsBuilder(original),
    }),
  );
}

class _SelectablePageTransitionsBuilder extends PageTransitionsBuilder {
  final PageTransitionsTheme original;

  const _SelectablePageTransitionsBuilder(this.original);

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    return original.buildTransitions<T>(route, context, animation, secondaryAnimation, SelectionArea(child: child));
  }
}
