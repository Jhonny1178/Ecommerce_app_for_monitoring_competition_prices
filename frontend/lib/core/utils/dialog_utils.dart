import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import '../../main.dart';

class DialogUtils {

  static Future<bool> confirmRejection(BuildContext context, String? email) async {
    final confirmed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Czy na pewno chcesz odrzucić wniosek?'),
        content: SizedBox(
          width: 450,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Przed odrzuceniem warto skontaktować się z użytkownikiem:'),
              const SizedBox(height: 8),
              SelectableText(
                email ?? 'Brak adresu e-mail',
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 16),
              const Text(
                'Odrzucenie usunie wniosek, wygenerowane scrapery i przesłany plik. Tego nie można cofnąć. '
                'Konto zostanie, a użytkownik po zalogowaniu zobaczy informację o odrzuceniu i będzie mógł napisać do nas wiadomość.',
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Anuluj'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
              foregroundColor: Theme.of(context).colorScheme.onError,
            ),
            child: const Text('Tak, odrzuć wniosek'),
          ),
        ],
      ),
    );

    return confirmed == true;
  }

  static void showSettingsDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) {
        final colorScheme = Theme.of(context).colorScheme;

        return Dialog(
          backgroundColor: colorScheme.surface,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          child: Container(
            width: 450,
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text('Ustawienia systemu', style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: colorScheme.onSurface)),
                    IconButton(icon: Icon(Icons.close, color: colorScheme.onSurface), onPressed: () => Navigator.pop(context)),
                  ],
                ),
                const SizedBox(height: 24),
                ValueListenableBuilder<ThemeMode>(
                  valueListenable: globalThemeNotifier,
                  builder: (context, currentMode, child) {
                    bool isDark = currentMode == ThemeMode.dark ||
                                 (currentMode == ThemeMode.system && Theme.of(context).brightness == Brightness.dark);

                    return SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('Tryb ciemny (Dark Mode)', style: TextStyle(fontWeight: FontWeight.bold, color: colorScheme.onSurface)),
                      subtitle: Text('Zmienia schemat kolorów aplikacji', style: TextStyle(color: colorScheme.onSurfaceVariant)),
                      value: isDark,
                      activeColor: colorScheme.primary,
                      onChanged: (bool value) {
                        globalThemeNotifier.value = value ? ThemeMode.dark : ThemeMode.light;
                      },
                    );
                  },
                ),
              ],
            ),
          ),
        );
      }
    );
  }
  static Future<void> showReportBugDialog(BuildContext context) async {
    final TextEditingController bugController = TextEditingController();
    bool isSubmitting = false;

    await showDialog(
      context: context,
      builder: (BuildContext context) {
        return StatefulBuilder(
          builder: (context, setStateDialog) {
            final colorScheme = Theme.of(context).colorScheme;
            
            return AlertDialog(
              backgroundColor: colorScheme.surfaceContainerHigh,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              contentPadding: const EdgeInsets.all(24),
              content: SizedBox(
                width: 400,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.error, color: colorScheme.onSurfaceVariant, size: 24),
                    const SizedBox(height: 16),
                    const Text(
                      'Zgłoś błąd',
                      style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: bugController,
                      maxLines: 3,
                      decoration: InputDecoration(
                        filled: true,
                        fillColor: colorScheme.surfaceContainerHighest,
                        labelText: 'Twoje zgłoszenie',
                        hintText: 'Opisz błąd, którego doświadczyłeś/aś...',
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: BorderSide.none,
                        ),
                        suffixIcon: IconButton(
                          icon: const Icon(Icons.cancel_outlined, size: 20),
                          onPressed: () => bugController.clear(),
                        ),
                      ),
                    ),
                    const SizedBox(height: 24),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        TextButton(
                          onPressed: isSubmitting ? null : () => Navigator.of(context).pop(),
                          child: const Text('Wróć'),
                        ),
                        const SizedBox(width: 8),
                        FilledButton(
                          onPressed: isSubmitting
                              ? null
                              : () async {
                                  if (bugController.text.trim().isEmpty) return;
                                  
                                  setStateDialog(() => isSubmitting = true);
                                  
                                  try {
                                    final response = await http.post(
                                      Uri.parse("/api/report_bug"),
                                      headers: {
                                        'Content-Type': 'application/json',
                                        'Accept': 'application/json',
                                      },
                                      body: jsonEncode({'message': bugController.text.trim()}),
                                    );
                                    
                                    if (response.statusCode != 200) {
                                      throw Exception("Błąd serwera (Kod: ${response.statusCode})");
                                    }

                                    final data = jsonDecode(response.body);
                                    
                                    if (data['ok'] == true) {
                                      if (context.mounted) {
                                        Navigator.of(context).pop();
                                        ScaffoldMessenger.of(context).showSnackBar(
                                          const SnackBar(content: Text('Zgłoszenie wysłane!'), backgroundColor: Colors.green),
                                        );
                                      }
                                    } else {
                                      throw Exception(data['error'] ?? 'Nieznany błąd');
                                    }
                                  } catch (e) {
                                    if (context.mounted) {
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        SnackBar(content: Text('Błąd: $e'), backgroundColor: Colors.red),
                                      );
                                    }
                                  } finally {
                                    setStateDialog(() => isSubmitting = false);
                                  }
                                },
                          style: FilledButton.styleFrom(
                            backgroundColor: colorScheme.primaryContainer,
                            foregroundColor: colorScheme.onPrimaryContainer,
                          ),
                          child: isSubmitting
                              ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                              : const Text('Wyślij zgłoszenie'),
                        ),
                      ],
                    )
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }
}