// Ekran startowy: przywraca zalogowaną sesję po odświeżeniu lub ponownym otwarciu strony.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';

import 'auth_router.dart';
import 'landing_page_screen.dart';

class SessionGateScreen extends StatefulWidget {
  const SessionGateScreen({super.key});

  @override
  State<SessionGateScreen> createState() => _SessionGateScreenState();
}

class _SessionGateScreenState extends State<SessionGateScreen> {
  late final Future<Map<String, dynamic>?> _user = _fetchUser();

  // Zalogowany użytkownik z /api/me albo null, gdy brak ważnej sesji.
  Future<Map<String, dynamic>?> _fetchUser() async {
    try {
      final response = await ApiClient.get(
        Uri.parse('/api/me'),
        headers: {'Accept': 'application/json'},
        redirectOnUnauthorized: false,
      );

      if (response.statusCode != 200) return null;

      final data = jsonDecode(response.body);
      if (data is Map && data['ok'] == true && data['user'] is Map) {
        return Map<String, dynamic>.from(data['user'] as Map);
      }
    } catch (_) {}

    return null;
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Map<String, dynamic>?>(
      future: _user,
      builder: (context, snapshot) {
        if (snapshot.connectionState != ConnectionState.done) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }

        final user = snapshot.data;
        if (user == null) return const LandingPageScreen();

        return AuthRouter(
          status: user['status']?.toString() ?? 'pending_admin',
          isAdmin: user['is_admin'] == true,
        );
      },
    );
  }
}
