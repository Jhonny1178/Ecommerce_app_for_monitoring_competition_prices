// Szczegóły raportu zmian; pozycja otwiera stronę produktu.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';

import '../../products/screens/product_details_screen.dart';
import '../../products/widgets/product_tiles.dart';
import 'change_reports_screen.dart';

const _sections = [
  ('price', 'Zmiany cen'),
  ('new', 'Nowe oferty konkurencji'),
  ('removed', 'Wycofane oferty'),
];

class ChangeReportScreen extends StatefulWidget {
  final int reportId;
  final String title;

  const ChangeReportScreen({super.key, required this.reportId, required this.title});

  @override
  State<ChangeReportScreen> createState() => _ChangeReportScreenState();
}

class _ChangeReportScreenState extends State<ChangeReportScreen> {
  bool _isLoading = true;
  String? _error;
  List<dynamic> _items = [];
  List<dynamic> _unchecked = [];

  @override
  void initState() {
    super.initState();
    _fetchReport();
  }

  Future<void> _fetchReport() async {
    try {
      final response = await ApiClient.get(
        Uri.parse('/api/change-reports/${widget.reportId}'),
        headers: {'Accept': 'application/json'},
      );
      final data = jsonDecode(response.body);

      if (!mounted) return;
      setState(() {
        if (response.statusCode == 200 && data['ok'] == true) {
          _items = data['data']['items'] ?? [];
          _unchecked = data['data']['unchecked_stores'] ?? [];
        } else {
          _error = data['error']?.toString() ?? 'Nie udało się pobrać raportu.';
        }
      });
    } catch (e) {
      debugPrint("Błąd pobierania raportu zmian: $e");
      if (mounted) setState(() => _error = 'Nie udało się pobrać raportu.');
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!, style: TextStyle(color: colorScheme.error)))
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    if (_unchecked.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 16),
                        child: Text(
                          "Nie sprawdzono sklepów: ${_unchecked.join(', ')}. "
                          "Ich oferty pochodzą z wcześniejszej aktualizacji.",
                          style: TextStyle(color: colorScheme.error),
                        ),
                      ),
                    if (_items.isEmpty)
                      const Padding(
                        padding: EdgeInsets.only(top: 48),
                        child: Center(child: Text(noChangesText)),
                      ),
                    for (final (type, label) in _sections)
                      ..._buildSection(type, label, colorScheme),
                  ],
                ),
    );
  }

  List<Widget> _buildSection(String type, String label, ColorScheme colorScheme) {
    final items = _items.where((item) => item['type'] == type).toList();
    if (items.isEmpty) return [];

    return [
      Padding(
        padding: const EdgeInsets.only(top: 8, bottom: 4),
        child: Text(
          "$label (${items.length})",
          style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: colorScheme.secondary),
        ),
      ),
      for (final item in items) ...[
        ChangeItemRow(item: item),
        Divider(height: 1, color: colorScheme.outlineVariant.withOpacity(0.3)),
      ],
      const SizedBox(height: 16),
    ];
  }
}

class ChangeItemRow extends StatelessWidget {
  final dynamic item;

  const ChangeItemRow({super.key, required this.item});

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final int? productId = item['product_id'];
    final String type = item['type']?.toString() ?? '';
    final String changedBy = item['changed_by']?.toString() ?? '';
    final String size = item['size']?.toString() ?? '';
    final String name = item['client_name']?.toString() ?? item['client_sku']?.toString() ?? 'Produkt';
    final String competitor = item['competitor_name']?.toString() ?? '';

    final lines = <Widget>[];
    void line(String text, {Color? color}) => lines.add(
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(text, style: TextStyle(fontSize: 13, color: color ?? colorScheme.onSurface)),
          ),
        );

    final num? oldCompetitor = toNum(item['old_competitor_price']);
    final num? newCompetitor = toNum(item['new_competitor_price']);
    final num? oldClient = toNum(item['old_client_price']);
    final num? newClient = toNum(item['new_client_price']);
    final num? oldDiff = toNum(item['old_difference']);
    final num? newDiff = toNum(item['new_difference']);

    if (type == 'price') {
      if (changedBy == 'competitor' || changedBy == 'both') {
        line("Cena konkurencji: ${price(oldCompetitor)} → ${price(newCompetitor)}");
      }
      if (changedBy == 'client' || changedBy == 'both') {
        line("Twoja cena: ${price(oldClient)} → ${price(newClient)}");
      }
      line("Różnica: ${_difference(oldDiff)} → ${_difference(newDiff)}", color: _differenceColor(newDiff, colorScheme));
    } else if (type == 'new') {
      line("Cena konkurencji: ${price(newCompetitor)}");
      line("Różnica: ${_difference(newDiff)}", color: _differenceColor(newDiff, colorScheme));
    } else if (type == 'removed') {
      line("Ostatnia cena konkurencji: ${price(oldCompetitor)}");
    }

    return InkWell(
      key: Key('change_item_${item['type']}_${item['client_sku']}_${item['store']}'),
      onTap: productId == null
          ? null
          : () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => ProductDetailsScreen(productId: productId)),
              ),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 8),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    size.isEmpty ? name : "$name · $size",
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    "Sklep: ${item['store'] ?? '-'}${competitor.isEmpty ? '' : ' · $competitor'}",
                    style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12),
                  ),
                  ...lines,
                  if (productId == null)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        "Produktu nie ma już w Twoim sklepie.",
                        style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12),
                      ),
                    ),
                ],
              ),
            ),
            if (productId != null) Icon(Icons.chevron_right, color: colorScheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }

  static String _difference(num? value) => value == null ? '-' : signed(value, ' zł');

  static Color? _differenceColor(num? value, ColorScheme colorScheme) {
    if (value == null || value == 0) return null;
    return value > 0 ? Colors.green.shade700 : colorScheme.error;
  }
}
