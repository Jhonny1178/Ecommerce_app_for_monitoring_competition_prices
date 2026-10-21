import 'package:flutter/foundation.dart' show visibleForTesting;
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:tonten/core/api/client_factory_io.dart'
    if (dart.library.js_interop) 'package:tonten/core/api/client_factory_web.dart';
import 'package:tonten/features/auth/screens/login_screen.dart';

class ApiClient {
  static GlobalKey<NavigatorState> navigatorKey =
      GlobalKey<NavigatorState>();

  static http.Client _client = createPlatformClient();

  static bool _isHandlingUnauthorized = false;

  @visibleForTesting
  static void setClient(http.Client client) {
    _client = client;
  }

  @visibleForTesting
  static void resetClient() {
    _client = createPlatformClient();
  }

  static const String baseUrl = 'http://localhost:6767';

  static Uri _buildUri(Uri url) {
    if (url.hasScheme) {
      return url;
    }

    return Uri.parse('$baseUrl${url.toString()}');
  }

  static void _handleUnauthorized() {
    if (_isHandlingUnauthorized) {
      return;
    }

    _isHandlingUnauthorized = true;

    WidgetsBinding.instance.addPostFrameCallback((_) {
      final context = navigatorKey.currentContext;

      if (context == null) {
        _isHandlingUnauthorized = false;
        return;
      }

      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Sesja wygasła. Zaloguj się ponownie.',
          ),
          backgroundColor: Colors.red,
        ),
      );

      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(
          builder: (_) => const LoginScreen(),
        ),
        (route) => false,
      );

      Future.delayed(
        const Duration(seconds: 2),
        () {
          _isHandlingUnauthorized = false;
        },
      );
    });
  }

  static Future<http.Response> get(
    Uri url, {
    Map<String, String>? headers,
  }) async {
    final absoluteUrl = _buildUri(url);

    final response = await _client.get(
      absoluteUrl,
      headers: headers,
    );

    if (response.statusCode == 401) {
      _handleUnauthorized();
    }

    return response;
  }

  static Future<http.Response> post(
    Uri url, {
    Map<String, String>? headers,
    Object? body,
  }) async {
    final absoluteUrl = _buildUri(url);

    final response = await _client.post(
      absoluteUrl,
      headers: headers,
      body: body,
    );

    if (response.statusCode == 401) {
      _handleUnauthorized();
    }

    return response;
  }
}