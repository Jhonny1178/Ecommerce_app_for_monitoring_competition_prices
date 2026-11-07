// Zakładka „Raporty zmian”: lista raportów po aktualizacjach danych.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:tonten/core/api/api_client.dart';

import '../../auth/screens/subscription_screen.dart';
import 'change_report_screen.dart';

/// "1 zmiana ceny", "3 zmiany cen", "5 zmian cen".
String plural(int count, String one, String few, String many) {
  final mod10 = count % 10;
  final mod100 = count % 100;
  if (count == 1) return '1 $one';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return '$count $few';
  return '$count $many';
}

/// Data i godzina aktualizacji w czasie lokalnym, np. "08.11.2026, 10:23".
String formatUpdateDate(dynamic value) {
  final parsed = value == null ? null : DateTime.tryParse(value.toString());
  if (parsed == null) return value?.toString() ?? '-';

  final local = parsed.toLocal();
  String two(int number) => number.toString().padLeft(2, '0');
  return "${two(local.day)}.${two(local.month)}.${local.year}, ${two(local.hour)}:${two(local.minute)}";
}

const noChangesText = 'Nie nastąpiły żadne zmiany od poprzedniej aktualizacji.';

class ChangeReportsScreen extends StatefulWidget {
  const ChangeReportsScreen({super.key});

  @override
  State<ChangeReportsScreen> createState() => _ChangeReportsScreenState();
}

class _ChangeReportsScreenState extends State<ChangeReportsScreen> {
  bool _isLoading = true;
  bool _locked = false;
  String _subscriptionPlan = 'Podstawowy';
  String? _error;
  List<dynamic> _reports = [];

  @override
  void initState() {
    super.initState();
    _fetchReports();
  }

  Future<void> _fetchReports() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      final response = await ApiClient.get(Uri.parse('/api/change-reports'), headers: {'Accept': 'application/json'});
      final data = jsonDecode(response.body);

      if (!mounted) return;
      setState(() {
        _locked = response.statusCode == 403 && data['locked'] == true;
        if (_locked) {
          _subscriptionPlan = data['subscription_plan']?.toString() ?? _subscriptionPlan;
        } else if (response.statusCode == 200 && data['ok'] == true) {
          _reports = data['data'] ?? [];
        } else {
          _error = data['error']?.toString() ?? 'Nie udało się pobrać raportów zmian.';
        }
      });
    } catch (e) {
      debugPrint("Błąd pobierania raportów zmian: $e");
      if (mounted) setState(() => _error = 'Nie udało się pobrać raportów zmian.');
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _openPlans() async {
    final changed = await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => SubscriptionScreen(isChangingPlan: true, currentPlan: _subscriptionPlan),
      ),
    );
    if (changed == true) _fetchReports();
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    if (_isLoading) return const Center(child: CircularProgressIndicator());
    if (_locked) return Align(alignment: Alignment.topCenter, child: _buildLockedPanel(colorScheme));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          "Zmiany po aktualizacjach",
          style: GoogleFonts.overpass(fontSize: 22, fontWeight: FontWeight.bold, color: colorScheme.onSurface),
        ),
        const SizedBox(height: 4),
        Text(
          "Zmiany cen i różnic oraz nowe i wycofane oferty konkurencji dla dopasowanych produktów.",
          style: TextStyle(color: colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: 16),
        Expanded(
          child: _error != null
              ? Center(child: Text(_error!, style: TextStyle(color: colorScheme.error)))
              : _reports.isEmpty
                  ? const Center(
                      child: Text("Brak raportów. Pierwszy raport pojawi się po kolejnej aktualizacji danych."),
                    )
                  : ListView.separated(
                      itemCount: _reports.length,
                      separatorBuilder: (context, index) => const SizedBox(height: 12),
                      itemBuilder: (context, index) => _buildReportTile(_reports[index], colorScheme),
                    ),
        ),
      ],
    );
  }

  Widget _buildReportTile(dynamic report, ColorScheme colorScheme) {
    final int priceChanges = report['price_changes'] ?? 0;
    final int newOffers = report['new_offers'] ?? 0;
    final int removedOffers = report['removed_offers'] ?? 0;
    final List<dynamic> unchecked = report['unchecked_stores'] ?? [];
    final String title = "Aktualizacja ${formatUpdateDate(report['created_at'])}";

    final summary = [
      if (priceChanges > 0) plural(priceChanges, 'zmiana ceny', 'zmiany cen', 'zmian cen'),
      if (newOffers > 0) plural(newOffers, 'nowa oferta', 'nowe oferty', 'nowych ofert'),
      if (removedOffers > 0) plural(removedOffers, 'wycofana oferta', 'wycofane oferty', 'wycofanych ofert'),
    ];

    return InkWell(
      key: Key('change_report_${report['id']}'),
      borderRadius: BorderRadius.circular(16),
      onTap: () => Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => ChangeReportScreen(reportId: report['id'], title: title)),
      ),
      child: Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: colorScheme.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.4)),
        ),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: colorScheme.primaryContainer.withOpacity(0.6),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(Icons.update, color: colorScheme.primary),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
                  const SizedBox(height: 4),
                  Text(
                    summary.isEmpty ? noChangesText : summary.join(' · '),
                    style: TextStyle(color: colorScheme.onSurfaceVariant),
                  ),
                  if (unchecked.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      "Nie sprawdzono sklepów: ${unchecked.join(', ')}",
                      style: TextStyle(color: colorScheme.error, fontSize: 12),
                    ),
                  ],
                ],
              ),
            ),
            Icon(Icons.chevron_right, color: colorScheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }

  Widget _buildLockedPanel(ColorScheme colorScheme) {
    return Container(
      padding: const EdgeInsets.all(32),
      decoration: BoxDecoration(
        color: colorScheme.secondaryContainer.withOpacity(0.6),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: colorScheme.secondary.withOpacity(0.2)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.lock_outline, size: 56, color: colorScheme.secondary.withOpacity(0.7)),
          const SizedBox(height: 16),
          Text(
            "Raporty zmian",
            style: GoogleFonts.overpass(
              fontSize: 22,
              fontWeight: FontWeight.bold,
              color: colorScheme.onSecondaryContainer,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            "Po każdej aktualizacji danych raport pokazuje zmiany cen konkurencji oraz jej nowe "
            "i wycofane oferty. Ta funkcja jest dostępna w pakietach Pro i Premium.",
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 14, color: colorScheme.onSecondaryContainer.withOpacity(0.8)),
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            key: const Key('change_plan_reports'),
            onPressed: _openPlans,
            icon: const Icon(Icons.workspace_premium),
            label: const Text("Zmień pakiet"),
          ),
        ],
      ),
    );
  }
}
