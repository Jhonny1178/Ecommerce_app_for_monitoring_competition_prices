import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:tonten/core/api/api_client.dart';

import '../../products/screens/products_list_screen.dart';
import '../../changes/screens/change_reports_screen.dart';
import '../../../core/utils/dialog_utils.dart';
import '../../auth/screens/login_screen.dart';
import '../../auth/screens/file_upload_screen.dart';
import '../../auth/screens/subscription_screen.dart';
import '../../auth/screens/auth_router.dart';
import '../../auth/screens/reset_password_screen.dart';
import '../widgets/analytics_section.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  bool _isFabExpanded = false;
  int _selectedTabIndex = 0;
  // Przejście z komunikatu na Dashboardzie: "Zmatchowane produkty" od razu z filtrem podejrzanych dopasowań.
  bool _highRiskPreset = false;
  bool _isCheckingUserStatus = true;
  final TextEditingController _currentPasswordController = TextEditingController();
  final TextEditingController _newPasswordController = TextEditingController();
  final TextEditingController _repeatPasswordController = TextEditingController();
  String? _currentPasswordError;
  String? _newPasswordError;
  String? _repeatPasswordError;
  bool _isChangingPassword = false;
  int _refreshCounter = 0;
  Timer? _dataVersionTimer;
  String? _dataVersion;
  bool _dataVersionLoaded = false;
  bool _newDataAvailable = false;

  @override
  void initState() {
    super.initState();
    _checkUserStatus();
    _checkDataVersion();
    _dataVersionTimer = Timer.periodic(const Duration(minutes: 2), (_) => _checkDataVersion());
  }

  @override
  void dispose() {
    _dataVersionTimer?.cancel();
    super.dispose();
  }

  // Pierwsze wywołanie zapamiętuje wersję danych, kolejne pokazują pasek, gdy pipeline skończył nowy przebieg.
  Future<void> _checkDataVersion() async {
    try {
      final response = await ApiClient.get(
        Uri.parse('/api/data_version'),
        headers: {'Accept': 'application/json'},
      );
      if (response.statusCode != 200) return;

      final data = jsonDecode(response.body);
      if (data is! Map || data['ok'] != true || !mounted) return;

      final version = data['last_update']?.toString();
      if (!_dataVersionLoaded) {
        _dataVersion = version;
        _dataVersionLoaded = true;
      } else if (version != _dataVersion && !_newDataAvailable) {
        setState(() => _newDataAvailable = true);
      }
    } catch (e) {
      debugPrint('Błąd sprawdzania nowych danych: $e');
    }
  }

  // Ukrywa pasek i przyjmuje bieżącą wersję danych jako widzianą.
  void _acceptDataVersion() {
    setState(() => _newDataAvailable = false);
    _dataVersionLoaded = false;
    _checkDataVersion();
  }

  Future<void> _checkUserStatus() async {
    try {
      final response = await ApiClient.get(
        Uri.parse('/api/me'),
        headers: {'Accept': 'application/json'},
      );

      if (response.statusCode != 200) {
        if (mounted) {
          setState(() => _isCheckingUserStatus = false);
        }
        return;
      }

      final data = jsonDecode(response.body);

      if (data['ok'] != true) {
        if (mounted) {
          setState(() => _isCheckingUserStatus = false);
        }
        return;
      }

      final user = data['user'] ?? {};
      final status = user['status']?.toString();

      if (!mounted) return;

      if (status == 'onboarding_required') {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const FileUploadScreen()),
        );
        return;
      }

      setState(() => _isCheckingUserStatus = false);
    } catch (e) {
      debugPrint('Błąd sprawdzania statusu użytkownika: $e');

      if (mounted) {
        setState(() => _isCheckingUserStatus = false);
      }
    }
  }

  Future<void> _cancelSubscription() async {
    try {
      final response = await ApiClient.post(Uri.parse('/api/subscription/cancel'));
      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['ok'] == true) {
        if (mounted) {
          Navigator.of(context).pushReplacement(
            MaterialPageRoute(builder: (_) => const AuthRouter(status: 'awaiting_payment', isAdmin: false)),
          );
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['error'] ?? 'Błąd anulowania'), backgroundColor: Colors.red));
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Błąd połączenia: $e'), backgroundColor: Colors.red));
      }
    }
  }

  // Nowy _refreshCounter przebudowuje zakładki (w tym statystyki), więc pobierają dane od nowa.
  void _refreshData() {
    setState(() => _refreshCounter++);
    _acceptDataVersion();
    _checkUserStatus();
  }

  void _showRiskyMatches() {
    setState(() {
      _highRiskPreset = true;
      _selectedTabIndex = 1;
    });
  }

  Future<void> _openProfileOrOnboarding() async {
    try {
      final response = await ApiClient.get(Uri.parse('/api/me'), headers: {'Accept': 'application/json'});
      if (!mounted) return;
      
      if (response.statusCode != 200) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Błąd profilu'), backgroundColor: Colors.red));
        return;
      }
      
      final data = jsonDecode(response.body);
      final user = data['user'] ?? {};
      final status = user['status']?.toString();
      
      if (status == 'onboarding_required') {
        Navigator.of(context).push(MaterialPageRoute(builder: (_) => const FileUploadScreen()));
        return;
      }
      
      final onboardingRequest = data['onboarding_request'] ?? {};

      showDialog(
        context: context,
        builder: (dialogContext) {
          final colorScheme = Theme.of(dialogContext).colorScheme;

          return AlertDialog(
            backgroundColor: colorScheme.surface,
            contentPadding: EdgeInsets.zero,
            content: SizedBox(
              width: 800,
              height: 600,
              child: DefaultTabController(
                length: 3,
                child: Column(
                  children: [
                    Container(
                      padding: const EdgeInsets.all(16),
                      color: colorScheme.surfaceContainerHighest.withOpacity(0.5),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Profil i Ustawienia', style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: colorScheme.onSurface)),
                          IconButton(icon: Icon(Icons.close, color: colorScheme.onSurface), onPressed: () => Navigator.pop(dialogContext)),
                        ],
                      ),
                    ),
                    TabBar(
                      labelColor: colorScheme.primary,
                      unselectedLabelColor: colorScheme.onSurfaceVariant,
                      indicatorColor: colorScheme.primary,
                      tabs: const [
                        Tab(text: 'Dane konta i sklepu', icon: Icon(Icons.person)),
                        Tab(text: 'Subskrypcja', icon: Icon(Icons.payment)),
                        Tab(text: 'Bezpieczeństwo', icon: Icon(Icons.security)),
                      ],
                    ),
                    Expanded(
                      child: TabBarView(
                        children: [
                          // Zakładka 1: Dane
                          SingleChildScrollView(
                            padding: const EdgeInsets.all(24),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                _dialogSectionTitle('Dane użytkownika', colorScheme),
                                _infoLine('Imię i nazwisko', '${user['first_name'] ?? '-'} ${user['last_name'] ?? ''}'),
                                _infoLine('Email', '${user['email'] ?? user['username'] ?? '-'}'),
                                const Divider(),
                                _dialogSectionTitle('Dane sklepu', colorScheme),
                                _infoLine('Nazwa sklepu', '${onboardingRequest['requested_store_name'] ?? user['client_name'] ?? '-'}'),
                                _infoLine('Domena', '${onboardingRequest['company_domain'] ?? user['company_domain'] ?? '-'}'),
                                const Divider(),
                                FilledButton.icon(
                                  onPressed: () {
                                    Navigator.pop(dialogContext);
                                    Navigator.of(context).push(MaterialPageRoute(builder: (_) => const FileUploadScreen()));
                                  },
                                  icon: const Icon(Icons.edit),
                                  label: const Text('Edytuj konfigurację techniczną'),
                                ),
                              ],
                            ),
                          ),
                          // Zakładka 2: Płatności
                          Padding(
                            padding: const EdgeInsets.all(32.0),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Icon(Icons.stars, size: 64, color: Colors.amber),
                                const SizedBox(height: 16),
                                Text('Twój aktualny plan:', style: TextStyle(fontSize: 16, color: colorScheme.onSurfaceVariant)),
                                Text('${user['subscription_plan'] ?? 'Brak'}', style: TextStyle(fontSize: 32, fontWeight: FontWeight.bold, color: colorScheme.primary)),
                                const SizedBox(height: 24),
                                Text('Zarządzanie płatnościami pozwala na zmianę lub anulowanie subskrypcji systemu e-ROCH.', style: TextStyle(color: colorScheme.onSurface)),
                                const SizedBox(height: 32),
                                Row(
                                  children: [
                                    FilledButton.icon(
                                      onPressed: () async {
                                        Navigator.pop(dialogContext);
                                        final changed = await Navigator.of(context).push(
                                          MaterialPageRoute(
                                            builder: (_) => SubscriptionScreen(
                                              isChangingPlan: true,
                                              currentPlan: user['subscription_plan']?.toString(),
                                            ),
                                          ),
                                        );
                                        if (changed == true) {
                                          setState(() => _refreshCounter++);
                                          _openProfileOrOnboarding();
                                        }
                                      },
                                      icon: const Icon(Icons.swap_horiz),
                                      label: const Text('Zmień pakiet'),
                                    ),
                                    const SizedBox(width: 16),
                                    OutlinedButton.icon(
                                      onPressed: () {
                                        showDialog(
                                          context: context,
                                          builder: (ctx) => AlertDialog(
                                            title: const Text('Anuluj subskrypcję'),
                                            content: const Text('Czy na pewno chcesz anulować subskrypcję? Stracisz natychmiastowy dostęp do systemu.'),
                                            actions: [
                                              TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Nie')),
                                              TextButton(
                                                onPressed: () {
                                                  Navigator.pop(ctx);
                                                  Navigator.pop(dialogContext);
                                                  _cancelSubscription();
                                                },
                                                child: const Text('Tak, anuluj', style: TextStyle(color: Colors.red)),
                                              ),
                                            ]
                                          )
                                        );
                                      },
                                      icon: const Icon(Icons.cancel, color: Colors.red),
                                      label: const Text('Anuluj subskrypcję', style: TextStyle(color: Colors.red)),
                                      style: OutlinedButton.styleFrom(side: const BorderSide(color: Colors.red)),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                          // Zakładka 3: Bezpieczeństwo
                          StatefulBuilder(
                            builder: (context, setTabState) {
                              return Padding(
                                padding: const EdgeInsets.all(32.0),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text('Zmiana hasła', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: colorScheme.onSurface)),
                                    const SizedBox(height: 16),
                                    TextField(
                                      controller: _currentPasswordController,
                                      obscureText: true,
                                      onChanged: (val) => setTabState(() => _currentPasswordError = null),
                                      decoration: InputDecoration(
                                        labelText: 'Aktualne hasło',
                                        border: const OutlineInputBorder(),
                                        filled: true,
                                        fillColor: colorScheme.surfaceContainer,
                                        errorText: _currentPasswordError,
                                      ),
                                    ),
                                    const SizedBox(height: 16),
                                    TextField(
                                      controller: _newPasswordController,
                                      obscureText: true,
                                      onChanged: (val) => setTabState(() => _newPasswordError = null),
                                      decoration: InputDecoration(
                                        labelText: 'Nowe hasło',
                                        border: const OutlineInputBorder(),
                                        filled: true,
                                        fillColor: colorScheme.surfaceContainer,
                                        errorText: _newPasswordError,
                                      ),
                                    ),
                                    const SizedBox(height: 16),
                                    TextField(
                                      controller: _repeatPasswordController,
                                      obscureText: true,
                                      onChanged: (val) => setTabState(() => _repeatPasswordError = null),
                                      decoration: InputDecoration(
                                        labelText: 'Powtórz nowe hasło',
                                        border: const OutlineInputBorder(),
                                        filled: true,
                                        fillColor: colorScheme.surfaceContainer,
                                        errorText: _repeatPasswordError,
                                      ),
                                    ),
                                    const SizedBox(height: 24),
                                    FilledButton(
                                      onPressed: _isChangingPassword ? null : () async {
                                        final currentPassword = _currentPasswordController.text;
                                        final newPassword = _newPasswordController.text;
                                        final repeatPassword = _repeatPasswordController.text;
                                        bool hasError = false;

                                        if (currentPassword.isEmpty) {
                                          _currentPasswordError = 'Podaj aktualne hasło';
                                          hasError = true;
                                        }
                                        if (newPassword.length < 12 || !newPassword.contains(RegExp(r'\d'))) {
                                          _newPasswordError = 'Hasło musi mieć minimum 12 znaków i zawierać przynajmniej jedną cyfrę';
                                          hasError = true;
                                        }
                                        if (newPassword != repeatPassword) {
                                          _repeatPasswordError = 'Nowe hasła nie są identyczne';
                                          hasError = true;
                                        }
                                        
                                        if (hasError) {
                                          setTabState(() {});
                                          return;
                                        }

                                        setTabState(() => _isChangingPassword = true);
                                        try {
                                          final res = await ApiClient.post(
                                            Uri.parse('/api/change_password'),
                                            headers: {'Content-Type': 'application/json'},
                                            body: jsonEncode({
                                              'current_password': currentPassword,
                                              'new_password': newPassword
                                            }),
                                          );
                                          if (res.statusCode == 200) {
                                            _currentPasswordController.clear();
                                            _newPasswordController.clear();
                                            _repeatPasswordController.clear();
                                            if(context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Hasło zmienione pomyślnie'), backgroundColor: Colors.green));
                                          } else {
                                            final data = jsonDecode(res.body);
                                            if(context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['error'] ?? 'Błąd zmiany hasła'), backgroundColor: Colors.red));
                                          }
                                        } catch (e) {
                                          if(context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Błąd połączenia: $e'), backgroundColor: Colors.red));
                                        } finally {
                                          setTabState(() => _isChangingPassword = false);
                                        }
                                      },
                                      child: _isChangingPassword ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2)) : const Text('Zapisz nowe hasło'),
                                    ),
                                    const SizedBox(height: 16),
                                    TextButton(
                                      onPressed: () async {
                                        final email = user['email'] ?? user['username'];
                                        if (email == null || email.isEmpty) {
                                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Brak zapisanego adresu e-mail na koncie'), backgroundColor: Colors.red));
                                          return;
                                        }
                                        
                                        setTabState(() => _isChangingPassword = true);
                                        try {
                                          final response = await ApiClient.post(
                                            Uri.parse('/api/forgot_password'),
                                            headers: {'Content-Type': 'application/json'},
                                            body: jsonEncode({'email': email}),
                                          );
                                          
                                          if (response.statusCode == 200) {
                                            Navigator.pop(dialogContext); // Close dialog
                                            if (context.mounted) {
                                              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Kod został wysłany na powiązany e-mail!'), backgroundColor: Colors.green));
                                              Navigator.of(context).push(MaterialPageRoute(builder: (_) => ResetPasswordScreen(email: email)));
                                            }
                                          } else {
                                            final data = jsonDecode(response.body);
                                            if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['error'] ?? 'Wystąpił błąd'), backgroundColor: Colors.red));
                                          }
                                        } catch (e) {
                                          if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Błąd połączenia: $e'), backgroundColor: Colors.red));
                                        } finally {
                                          setTabState(() => _isChangingPassword = false);
                                        }
                                      },
                                      child: const Text('Zapomniałem hasła'),
                                    )
                                  ],
                                ),
                              );
                            }
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      );
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Błąd profilu: $e'), backgroundColor: Colors.red));
    }
  }

  void _logout() async {
    await ApiClient.post(Uri.parse('/api/logout'));

    if (mounted) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    if (_isCheckingUserStatus) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }

    return Scaffold(
      backgroundColor: colorScheme.surface,
      floatingActionButton: _buildExpandableFab(colorScheme),
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(colorScheme),
            if (_newDataAvailable) _buildNewDataBanner(colorScheme),
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(32),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _buildCustomTabBar(colorScheme),
                    const SizedBox(height: 32),
                    if (_selectedTabIndex == 0) ...[
                      AnalyticsSection(
                        key: ValueKey('analytics_$_refreshCounter'),
                        onShowRiskyMatches: _showRiskyMatches,
                      ),
                    ] else if (_selectedTabIndex == 1) ...[
                      SizedBox(
                        height: 800,
                        child: ProductsListScreen(
                          key: ValueKey('matched_${_refreshCounter}_$_highRiskPreset'),
                          matchedOnly: true,
                          initialHighRisk: _highRiskPreset,
                        ),
                      ),
                    ] else if (_selectedTabIndex == 2) ...[
                      SizedBox(
                        height: 800,
                        child: ProductsListScreen(key: ValueKey('all_$_refreshCounter'), matchedOnly: false),
                      ),
                    ] else if (_selectedTabIndex == 3) ...[
                      SizedBox(
                        height: 800,
                        child: ChangeReportsScreen(key: ValueKey('changes_$_refreshCounter')),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildNewDataBanner(ColorScheme colorScheme) {
    return Container(
      width: double.infinity,
      color: colorScheme.primaryContainer,
      padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 8),
      child: Row(
        children: [
          Icon(Icons.update, color: colorScheme.onPrimaryContainer),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              'Są nowe dane: zakończyła się aktualizacja cen.',
              style: TextStyle(color: colorScheme.onPrimaryContainer),
            ),
          ),
          FilledButton(
            onPressed: _refreshData,
            child: const Text('Odśwież dane'),
          ),
          IconButton(
            tooltip: 'Ukryj',
            icon: Icon(Icons.close, color: colorScheme.onPrimaryContainer),
            onPressed: _acceptDataVersion,
          ),
        ],
      ),
    );
  }

  Widget _buildHeader(ColorScheme colorScheme) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.only(top: 10, bottom: 10),
      decoration: BoxDecoration(
        border: Border(
          bottom: BorderSide(
            color: colorScheme.outlineVariant,
            width: 1,
          ),
        ),
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Text(
            'e-ROCH',
            style: GoogleFonts.overpass(
              fontSize: 64,
            ),
          ),
          Positioned(
            right: 16,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                IconButton(
                  icon: const Icon(Icons.refresh, size: 28),
                  tooltip: 'Odśwież',
                  onPressed: _refreshData,
                ),
                PopupMenuButton<String>(
                  offset: const Offset(0, 50),
                  icon: const Icon(Icons.account_circle_outlined, size: 28),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  onSelected: (value) {
                    if (value == 'profile') _openProfileOrOnboarding();
                    if (value == 'logout') _logout();
                  },
                  itemBuilder: (BuildContext context) =>
                      <PopupMenuEntry<String>>[
                    PopupMenuItem<String>(
                      value: 'profile',
                      child: Row(
                        children: [
                          Icon(
                            Icons.person_outline,
                            color: colorScheme.primary,
                            size: 20,
                          ),
                          const SizedBox(width: 12),
                          const Text(
                            'Profil / konfiguracja',
                            style: TextStyle(fontWeight: FontWeight.bold),
                          ),
                        ],
                      ),
                    ),
                    const PopupMenuDivider(),
                    PopupMenuItem<String>(
                      value: 'logout',
                      child: Row(
                        children: [
                          Icon(
                            Icons.logout,
                            color: colorScheme.error,
                            size: 20,
                          ),
                          const SizedBox(width: 12),
                          Text(
                            'Wyloguj się',
                            style: TextStyle(
                              color: colorScheme.error,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCustomTabBar(ColorScheme colorScheme) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 16),
      decoration: BoxDecoration(
        color: colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(30),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          _buildTabButton('Dashboard', 0, colorScheme),
          _buildTabButton('Zmatchowane produkty', 1, colorScheme),
          _buildTabButton('Wszystkie produkty', 2, colorScheme),
          _buildTabButton('Raporty zmian', 3, colorScheme),
        ],
      ),
    );
  }

  Widget _buildTabButton(String title, int index, ColorScheme colorScheme) {
    final isSelected = _selectedTabIndex == index;

    return InkWell(
      // Ręczny wybór zakładki = zwykła lista, bez filtra ustawionego z Dashboardu.
      onTap: () => setState(() {
        _selectedTabIndex = index;
        _highRiskPreset = false;
      }),
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
        decoration: BoxDecoration(
          color: isSelected ? colorScheme.primaryContainer : Colors.transparent,
          borderRadius: BorderRadius.circular(20),
        ),
        child: Text(
          title,
          style: TextStyle(
            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
            color: isSelected
                ? colorScheme.onPrimaryContainer
                : colorScheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }

  Widget _buildExpandableFab(ColorScheme colorScheme) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        AnimatedSize(
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeInOut,
          child: _isFabExpanded
              ? Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      FloatingActionButton.extended(
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(30),
                        ),
                        heroTag: 'reportErrorFab',
                        onPressed: () =>
                            DialogUtils.showReportBugDialog(context),
                        backgroundColor: colorScheme.primaryContainer,
                        foregroundColor: colorScheme.onPrimaryContainer,
                        elevation: 1,
                        icon: const Icon(Icons.error_outline),
                        label: const Text('Zgłoś błąd'),
                      ),
                      const SizedBox(height: 12),
                      FloatingActionButton.extended(
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(30),
                        ),
                        heroTag: 'settingsFabGlobal',
                        onPressed: () => DialogUtils.showSettingsDialog(context),
                        backgroundColor: colorScheme.primaryContainer,
                        foregroundColor: colorScheme.onPrimaryContainer,
                        elevation: 1,
                        icon: const Icon(Icons.settings_outlined),
                        label: const Text('Ustawienia'),
                      ),
                    ],
                  ),
                )
              : const SizedBox.shrink(),
        ),
        FloatingActionButton(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(30),
          ),
          heroTag: 'menuToggleFab',
          onPressed: () {
            setState(() {
              _isFabExpanded = !_isFabExpanded;
            });
          },
          backgroundColor: colorScheme.primary,
          foregroundColor: colorScheme.onPrimary,
          elevation: 2,
          child: AnimatedSwitcher(
            duration: const Duration(milliseconds: 300),
            transitionBuilder: (child, animation) => ScaleTransition(
              scale: animation,
              child: FadeTransition(
                opacity: animation,
                child: child,
              ),
            ),
            child: Icon(
              _isFabExpanded ? Icons.close : Icons.menu,
              key: ValueKey<bool>(_isFabExpanded),
            ),
          ),
        ),
      ],
    );
  }

  Widget _dialogSectionTitle(String title, ColorScheme colorScheme) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(
        title,
        style: TextStyle(
          color: colorScheme.primary,
          fontWeight: FontWeight.bold,
          fontSize: 16,
          decoration: TextDecoration.none,
        ),
      ),
    );
  }

  Widget _infoLine(String label, String value) {
    final normalizedValue = value.trim().isEmpty ? '-' : value;
    final colorScheme = Theme.of(context).colorScheme;

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 150,
            child: Text(
              '$label:',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w700,
                color: colorScheme.onSurface,
                decoration: TextDecoration.none,
              ),
            ),
          ),
          Expanded(
            child: SelectableText(
              normalizedValue,
              style: TextStyle(
                fontSize: 14,
                color: colorScheme.onSurfaceVariant,
                decoration: TextDecoration.none,
              ),
            ),
          ),
        ],
      ),
    );
  }
}