import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:tonten/core/api/api_client.dart';
import 'login_screen.dart';

class ResetPasswordScreen extends StatefulWidget {
  final String email;

  const ResetPasswordScreen({super.key, required this.email});

  @override
  State<ResetPasswordScreen> createState() => _ResetPasswordScreenState();
}

class _ResetPasswordScreenState extends State<ResetPasswordScreen> {
  final _codeController = TextEditingController();
  final _newPasswordController = TextEditingController();
  final _repeatPasswordController = TextEditingController();
  String? _codeError;
  String? _newPasswordError;
  String? _repeatPasswordError;
  bool _isLoading = false;
  bool _isPasswordObscured = true;

  Future<void> _resetPassword() async {
    final code = _codeController.text.trim();
    final newPassword = _newPasswordController.text.trim();
    final repeatPassword = _repeatPasswordController.text.trim();
    bool hasError = false;

    if (code.isEmpty) {
      _codeError = 'Podaj kod';
      hasError = true;
    }
    
    if (newPassword.length < 12 || !newPassword.contains(RegExp(r'\d'))) {
      _newPasswordError = 'Hasło musi mieć minimum 12 znaków i zawierać przynajmniej jedną cyfrę';
      hasError = true;
    }
    
    if (newPassword != repeatPassword) {
      _repeatPasswordError = 'Hasła nie są takie same';
      hasError = true;
    }
    
    if (hasError) {
      setState(() {});
      return;
    }

    setState(() => _isLoading = true);

    try {
      final response = await ApiClient.post(
        Uri.parse('/api/reset_password'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'email': widget.email,
          'code': code,
          'new_password': newPassword,
        }),
      );

      final data = jsonDecode(response.body);

      if (response.statusCode == 200 && data['ok'] == true) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Hasło zostało pomyślnie zmienione! Możesz się teraz zalogować.'), backgroundColor: Colors.green),
          );
          Navigator.of(context).pushAndRemoveUntil(
            MaterialPageRoute(builder: (_) => const LoginScreen()),
            (route) => false,
          );
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(data['error'] ?? 'Wystąpił błąd'), backgroundColor: Colors.red),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Błąd połączenia: $e'), backgroundColor: Colors.red),
        );
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  void dispose() {
    _codeController.dispose();
    _newPasswordController.dispose();
    _repeatPasswordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: colorScheme.surface,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        iconTheme: IconThemeData(color: colorScheme.onSurface),
      ),
      body: Center(
        child: SingleChildScrollView(
          child: Container(
            width: 400,
            padding: const EdgeInsets.all(32),
            decoration: BoxDecoration(
              color: colorScheme.surfaceContainerHigh,
              borderRadius: BorderRadius.circular(20),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Nowe hasło',
                  style: GoogleFonts.overpass(fontSize: 32, fontWeight: FontWeight.bold),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 16),
                Text(
                  'Wpisz 6-cyfrowy kod wysłany na adres:\n${widget.email}',
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontWeight: FontWeight.w500),
                ),
                const SizedBox(height: 32),
                TextField(
                  controller: _codeController,
                  onChanged: (val) => setState(() => _codeError = null),
                  decoration: InputDecoration(
                    labelText: '6-cyfrowy kod',
                    border: const OutlineInputBorder(),
                    errorText: _codeError,
                  ),
                  maxLength: 6,
                  keyboardType: TextInputType.number,
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _newPasswordController,
                  obscureText: _isPasswordObscured,
                  onChanged: (val) => setState(() => _newPasswordError = null),
                  decoration: InputDecoration(
                    labelText: 'Nowe hasło',
                    border: const OutlineInputBorder(),
                    errorText: _newPasswordError,
                    suffixIcon: IconButton(
                      icon: Icon(_isPasswordObscured ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                      onPressed: () => setState(() => _isPasswordObscured = !_isPasswordObscured),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _repeatPasswordController,
                  obscureText: _isPasswordObscured,
                  onChanged: (val) => setState(() => _repeatPasswordError = null),
                  decoration: InputDecoration(
                    labelText: 'Powtórz nowe hasło',
                    border: const OutlineInputBorder(),
                    errorText: _repeatPasswordError,
                  ),
                ),
                const SizedBox(height: 32),
                FilledButton(
                  onPressed: _isLoading ? null : _resetPassword,
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 20),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(30)),
                  ),
                  child: _isLoading
                      ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                      : const Text('Zapisz nowe hasło'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
