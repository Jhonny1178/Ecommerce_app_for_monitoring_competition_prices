import 'package:flutter/material.dart';
import 'core/theme/theme.dart';
import 'features/auth/screens/session_gate_screen.dart';
import 'core/api/api_client.dart';
import 'core/widgets/selectable_text_theme.dart';

final ValueNotifier<ThemeMode> globalThemeNotifier = ValueNotifier(ThemeMode.light);

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: globalThemeNotifier,
      builder: (context, currentMode, child) {
        return MaterialApp(
          navigatorKey: ApiClient.navigatorKey,
          debugShowCheckedModeBanner: false,
          title: 'e-ROCH App',
          
          themeMode: currentMode,
          theme: withSelectableText(AppTheme.lightTheme),
          darkTheme: withSelectableText(AppTheme.darkTheme),
          home: const SessionGateScreen(),
        );
      },
    );
  }
}