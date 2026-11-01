import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/core/utils/validators.dart';
import 'dart:convert';
import 'auth_router.dart';

class RegisterScreenTwo extends StatefulWidget {
  final VoidCallback onBack;
  final VoidCallback onSuccess;
  final String name;
  final String surname;
  final String email;
  final String password;

  const RegisterScreenTwo({
    super.key, 
    required this.onBack,
    required this.onSuccess,
    required this.name,
    required this.surname,
    required this.email,
    required this.password,
    });

  @override
  State<RegisterScreenTwo> createState() => _RegisterScreenTwoState();
}

class _RegisterScreenTwoState extends State<RegisterScreenTwo> {
  final _mainDomainController = TextEditingController();

  final List<TextEditingController> _competitors = [TextEditingController()];

  bool _isLoading = false;
  bool _showUrlErrors = false;

  Future<void> _logInAfterRegistration() async {
    try {
      final response = await ApiClient.post(
        Uri.parse("/api/login"),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'username': widget.email, 'password': widget.password}),
      );
      final data = jsonDecode(response.body);

      if (!mounted) return;

      if (response.statusCode == 200 && data['ok'] == true) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Konto zostało utworzone. Analizujemy podane sklepy, a w tym czasie możesz wypełnić formularz.'),
            backgroundColor: Colors.green,
          ),
        );
        Navigator.of(context).pushAndRemoveUntil(
          MaterialPageRoute(
            builder: (_) => AuthRouter(
              status: data['status']?.toString() ?? 'onboarding_required',
              isAdmin: false,
            ),
          ),
          (route) => false,
        );
        return;
      }
    } catch (e) {
      debugPrint('Automatyczne logowanie nie powiodło się: $e');
    }

    if (!mounted) return;

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Konto zostało utworzone. Zaloguj się, aby wypełnić dalsze kroki.'),
        backgroundColor: Colors.green,
      ),
    );
    widget.onSuccess();
  }

  Future<void> _submitRegistration() async {
    if (_competitors.any((controller) => !Validators.isWebsiteUrl(controller.text))) {
      setState(() => _showUrlErrors = true);
      return;
    }

    setState(() => _isLoading = true);

    try {
      final url = Uri.parse("/api/register");
      final response = await ApiClient.post(
        url,
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'first_name': widget.name,
          'last_name': widget.surname,
          'company_domain': _mainDomainController.text, 
          'email': widget.email,
          'password': widget.password,
          'urls': _competitors.map((c) => c.text.trim()).where((text) => text.isNotEmpty).toList(),
        }),
      );

      final data = jsonDecode(response.body);

      if (response.statusCode == 200 && data['ok'] == true) {
        await _logInAfterRegistration();
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(data['error'] ?? 'Błąd rejestracji'), backgroundColor: Theme.of(context).colorScheme.error),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Błąd połączenia: $e'), backgroundColor: Theme.of(context).colorScheme.error),
      );
    } finally {
      if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  @override
  void dispose() {
    _mainDomainController.dispose();
    for (var controller in _competitors) {
      controller.dispose();
    }
    super.dispose();
  }

  void _addCompetitor() {
    if (_competitors.length < 5) {
      setState(() {
        _competitors.add(TextEditingController());
      });
    }
  }

  void _removeCompetitor(int index) {
    setState(() {
      _competitors[index].dispose();
      _competitors.removeAt(index);
    });
  }

  bool get _isValid {
    if (_mainDomainController.text.isEmpty) return false;
    for (var controller in _competitors) {
      if (controller.text.isEmpty) return false;
    }
    return true;
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Padding(
      padding: const EdgeInsets.all(40.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            controller: _mainDomainController,
            onChanged: (val) => setState(() {}),
            decoration: InputDecoration(
              filled: true,
              fillColor: colorScheme.surfaceContainerHigh,
              labelText: 'Domena Twojego sklepu',
              hintText: 'Wpisz domenę swojego sklepu...',
              border: const UnderlineInputBorder(),
            ),
          ),
          const SizedBox(height: 24),

          Container(
            margin: const EdgeInsets.only(bottom: 16),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: colorScheme.secondaryContainer,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline, color: colorScheme.onSecondaryContainer),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    'Po wysłaniu wniosku przeanalizujemy podane sklepy konkurencji. Status wniosku będzie widoczny na Twojej stronie po zalogowaniu.',
                    style: TextStyle(color: colorScheme.onSecondaryContainer),
                  ),
                ),
              ],
            ),
          ),

          if (_competitors.length < 5)
            Padding(
              padding: const EdgeInsets.only(bottom: 16.0),
              child: Row(
                children: [
                  InkWell(
                    onTap: _addCompetitor,
                    borderRadius: BorderRadius.circular(20),
                    child: CircleAvatar(
                      backgroundColor: colorScheme.primary,
                      radius: 16,
                      child: Icon(Icons.add, color: colorScheme.onPrimary, size: 20),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      decoration: BoxDecoration(
                        color: colorScheme.surface,
                        borderRadius: BorderRadius.circular(30),
                        border: Border.all(color: colorScheme.outlineVariant),
                      ),
                      child: Text(
                        'Dodaj 1-5 domen sklepów konkurencji',
                        style: TextStyle(color: colorScheme.onSurfaceVariant),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ...List.generate(_competitors.length, (index) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 16.0),
              child: Row(
                children: [
                  InkWell(
                    onTap: () => _removeCompetitor(index),
                    borderRadius: BorderRadius.circular(20),
                    child: CircleAvatar(
                      backgroundColor: colorScheme.primary,
                      radius: 16,
                      child: Icon(Icons.remove, color: colorScheme.onPrimary, size: 20),
                    ),
                  ),
                  const SizedBox(width: 12),

                  Expanded(
                    child: TextField(
                      controller: _competitors[index],
                      onChanged: (val) => setState(() {}),
                      decoration: InputDecoration(
                        filled: true,
                        fillColor: colorScheme.surfaceContainerHigh,
                        labelText: 'Domena sklepu konkurencji nr ${index + 1}',
                        hintText: 'Wpisz domenę sklepu konkurencji...',
                        border: const UnderlineInputBorder(),
                        errorText: _showUrlErrors && !Validators.isWebsiteUrl(_competitors[index].text)
                            ? Validators.invalidWebsite
                            : null,
                      ),
                    ),
                  ),
                ],
              ),
            );
          }),

          const SizedBox(height: 24),

          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              OutlinedButton.icon(
                onPressed: widget.onBack,
                icon: const Icon(Icons.undo),
                label: const Text('Wróć'),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
                  side: BorderSide(color: colorScheme.outline),
                ),
              ),
              const SizedBox(width: 16),

              Expanded(
                child: FilledButton(
                  onPressed: _isValid && !_isLoading ? _submitRegistration : null,
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 20),
                  ),
                  child: const Text(
                    'Wyślij wniosek o zarejestrowanie sklepu',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
            ],
          ),
        ],
      )
    );
  }
}