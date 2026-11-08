// Dashboard klienta: statystyki wszystkich dopasowań (pokrycie, pozycja cenowa, sklepy, kategorie i marki, największe różnice).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';

import '../../products/screens/product_details_screen.dart';
import '../../products/widgets/product_tiles.dart';

class AnalyticsSection extends StatefulWidget {
  // Klik w komunikat o pominiętych dopasowaniach: przejście do listy z filtrem podejrzanych.
  final VoidCallback onShowRiskyMatches;

  const AnalyticsSection({super.key, required this.onShowRiskyMatches});

  @override
  State<AnalyticsSection> createState() => _AnalyticsSectionState();
}

class _AnalyticsSectionState extends State<AnalyticsSection> {
  bool _isLoading = true;
  Map<String, dynamic> _data = {};
  bool _showBrands = false;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    try {
      final response = await ApiClient.get(Uri.parse('/api/dashboard'), headers: {'Accept': 'application/json'});
      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data is Map && data['ok'] == true && mounted) {
        setState(() => _data = Map<String, dynamic>.from(data));
      }
    } catch (e) {
      debugPrint('Błąd pobierania statystyk: $e');
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Map<String, dynamic> _map(dynamic value) => value is Map ? Map<String, dynamic>.from(value) : {};

  List<Map<String, dynamic>> _list(dynamic value) =>
      value is List ? value.whereType<Map>().map((item) => Map<String, dynamic>.from(item)).toList() : [];

  int _int(dynamic value) => toNum(value)?.round() ?? 0;

  String _percentOf(int part, int whole) => whole > 0 ? '${(part * 100 / whole).round()}%' : '0%';

  String _plural(int count, String one, String few, String many) {
    if (count == 1) return '1 $one';
    final mod10 = count % 10;
    final mod100 = count % 100;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return '$count $few';
    return '$count $many';
  }

  // Mediana różnicy (cena konkurencji - Twoja) słowami.
  String _typicalLabel(num? median) {
    if (median == null) return 'brak danych';
    if (median.abs() < 0.005) return 'ta sama cena';
    return "${median < 0 ? 'taniej' : 'drożej'} o ${median.abs().toStringAsFixed(2)} zł";
  }

  // Konkurencja taniej = czerwony (gorzej dla klienta), droższa = zielony.
  Color _diffColor(num? diff, ColorScheme colorScheme) {
    if (diff == null || diff.abs() < 0.005) return colorScheme.onSurfaceVariant;
    return diff > 0 ? Colors.green.shade700 : colorScheme.error;
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    if (_isLoading) {
      return const Padding(
        padding: EdgeInsets.all(48),
        child: Center(child: CircularProgressIndicator()),
      );
    }

    final summary = _map(_data['summary']);
    final matched = _int(summary['matched_products']);
    final total = _int(summary['total_products']);

    if (matched == 0) {
      return _panel(
        colorScheme,
        key: const Key('analytics_empty'),
        child: Column(
          children: [
            Icon(Icons.insights_outlined, size: 40, color: colorScheme.onSurfaceVariant),
            const SizedBox(height: 12),
            const Text('Brak dopasowanych ofert konkurencji', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
            const SizedBox(height: 4),
            Text(
              'Statystyki pojawią się, gdy pipeline dopasuje Twoje produkty do ofert sklepów konkurencji.',
              textAlign: TextAlign.center,
              style: TextStyle(color: colorScheme.onSurfaceVariant),
            ),
          ],
        ),
      );
    }

    final median = toNum(summary['median_diff']);
    final riskyOffers = _int(summary['risky_offers']);
    final gaps = _map(_data['biggest_gaps']);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: _kpiCard(
                colorScheme,
                title: 'Pokrycie',
                value: '$matched z $total',
                subtitle: 'wariantów ma ofertę konkurencji (${_percentOf(matched, total)})',
                icon: Icons.link,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: _kpiCard(
                colorScheme,
                title: 'Monitorowane sklepy',
                value: '${_int(summary['stores'])}',
                subtitle: 'sklepy z ofertami dopasowanymi do Twoich',
                icon: Icons.storefront,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: _kpiCard(
                colorScheme,
                key: const Key('typical_difference'),
                title: 'Konkurencja typowo',
                value: _typicalLabel(median),
                valueColor: _diffColor(median, colorScheme),
                subtitle: 'mediana różnicy cen, bez podejrzanych dopasowań',
                icon: Icons.balance,
              ),
            ),
          ],
        ),
        const SizedBox(height: 20),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(flex: 5, child: _positionPanel(colorScheme, _map(_data['position']))),
            const SizedBox(width: 20),
            Expanded(flex: 7, child: _storesPanel(colorScheme, _list(_data['stores']))),
          ],
        ),
        const SizedBox(height: 20),
        _segmentsPanel(colorScheme),
        const SizedBox(height: 20),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: _gapsPanel(
                colorScheme,
                key: const Key('gaps_competitor_cheaper'),
                title: 'Konkurencja dużo taniej',
                subtitle: 'Tu Twoja cena najbardziej odstaje w górę',
                items: _list(gaps['competitor_cheaper']),
              ),
            ),
            const SizedBox(width: 20),
            Expanded(
              child: _gapsPanel(
                colorScheme,
                key: const Key('gaps_you_cheaper'),
                title: 'Masz zapas cenowy',
                subtitle: 'Tu jesteś wyraźnie tańszy od konkurencji',
                items: _list(gaps['you_cheaper']),
              ),
            ),
          ],
        ),
        // Informacja pomocnicza: na samym dole i bez wyróżnienia.
        if (riskyOffers > 0) ...[
          const SizedBox(height: 16),
          _riskyNote(colorScheme, riskyOffers, _int(summary['risky_products']), _int(_data['risk_threshold_pct'] ?? 50)),
        ],
      ],
    );
  }

  Widget _panel(ColorScheme colorScheme, {Key? key, String? title, String? subtitle, Widget? trailing, required Widget child}) {
    return Container(
      key: key,
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: colorScheme.surfaceContainerHighest.withOpacity(0.65),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (title != null) ...[
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
                      if (subtitle != null) ...[
                        const SizedBox(height: 4),
                        Text(subtitle, style: TextStyle(fontSize: 12, color: colorScheme.onSurfaceVariant)),
                      ],
                    ],
                  ),
                ),
                if (trailing != null) trailing,
              ],
            ),
            const SizedBox(height: 16),
          ],
          child,
        ],
      ),
    );
  }

  Widget _kpiCard(
    ColorScheme colorScheme, {
    Key? key,
    required String title,
    required String value,
    required String subtitle,
    required IconData icon,
    Color? valueColor,
  }) {
    return Container(
      key: key,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 13, fontWeight: FontWeight.w600),
                ),
              ),
              Icon(icon, color: colorScheme.primary, size: 20),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            value,
            style: TextStyle(color: valueColor ?? colorScheme.onSurface, fontSize: 24, fontWeight: FontWeight.w900),
          ),
          const SizedBox(height: 4),
          Text(subtitle, style: TextStyle(color: colorScheme.onSurfaceVariant.withOpacity(0.8), fontSize: 11)),
        ],
      ),
    );
  }

  Widget _riskyNote(ColorScheme colorScheme, int offers, int variants, int threshold) {
    final offersText = _plural(offers, 'podejrzane dopasowanie', 'podejrzane dopasowania', 'podejrzanych dopasowań');
    final variantsText = variants == 1 ? '1 wariancie' : '$variants wariantach';

    return InkWell(
      key: const Key('risky_matches_note'),
      borderRadius: BorderRadius.circular(12),
      onTap: widget.onShowRiskyMatches,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Row(
          children: [
            Icon(Icons.info_outline, size: 16, color: colorScheme.onSurfaceVariant),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                'Pominięto $offersText w $variantsText (różnica ceny ponad $threshold%), '
                'bo mogą to być źle dopasowane produkty.',
                style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12),
              ),
            ),
            const SizedBox(width: 8),
            Text('Pokaż je', style: TextStyle(color: colorScheme.primary, fontSize: 12, fontWeight: FontWeight.w600)),
            Icon(Icons.chevron_right, size: 16, color: colorScheme.primary),
          ],
        ),
      ),
    );
  }

  Widget _positionPanel(ColorScheme colorScheme, Map<String, dynamic> position) {
    final cheapest = _int(position['you_cheapest']);
    final competitorCheaper = _int(position['competitor_cheaper']);
    final same = _int(position['same_price']);
    final total = cheapest + competitorCheaper + same;

    Widget bar(String label, int value, Color color) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(label, style: const TextStyle(fontWeight: FontWeight.w700)),
              const Spacer(),
              Text('$value (${_percentOf(value, total)})', style: const TextStyle(fontWeight: FontWeight.w800)),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              value: total > 0 ? value / total : 0,
              minHeight: 9,
              backgroundColor: colorScheme.surface,
              color: color,
            ),
          ),
        ],
      );
    }

    return _panel(
      colorScheme,
      title: 'Pozycja cenowa',
      subtitle: 'Twoja cena vs najtańsza oferta konkurencji (warianty)',
      child: Column(
        children: [
          bar('Jesteś najtańszy', cheapest, Colors.green.shade600),
          const SizedBox(height: 14),
          bar('Konkurencja taniej', competitorCheaper, colorScheme.error),
          const SizedBox(height: 14),
          bar('Ta sama cena', same, colorScheme.outline),
        ],
      ),
    );
  }

  Widget _tableRow(ColorScheme colorScheme, List<String> cells, {bool header = false, Color? lastColor}) {
    const flexes = [3, 2, 2, 2];
    final style = header
        ? TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: colorScheme.onSurfaceVariant)
        : const TextStyle(fontSize: 13);

    return Container(
      padding: const EdgeInsets.symmetric(vertical: 9),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: colorScheme.outlineVariant.withOpacity(header ? 0.6 : 0.25))),
      ),
      child: Row(
        children: [
          for (var i = 0; i < cells.length; i++)
            Expanded(
              flex: flexes[i],
              child: Text(
                cells[i],
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: i == 0 ? TextAlign.left : TextAlign.right,
                style: !header && i == cells.length - 1 && lastColor != null
                    ? style.copyWith(color: lastColor, fontWeight: FontWeight.w700)
                    : (!header && i == 0 ? style.copyWith(fontWeight: FontWeight.w600) : style),
              ),
            ),
        ],
      ),
    );
  }

  Widget _storesPanel(ColorScheme colorScheme, List<Map<String, dynamic>> stores) {
    return _panel(
      colorScheme,
      key: const Key('stores_panel'),
      title: 'Sklepy konkurencji',
      subtitle: 'Typowa różnica: mediana ceny sklepu względem Twojej',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _tableRow(colorScheme, const ['Sklep', 'Wspólne oferty', 'Taniej od Ciebie', 'Typowa różnica'], header: true),
          for (final store in stores)
            _tableRow(
              colorScheme,
              [
                store['store']?.toString() ?? '-',
                '${_int(store['offers'])}',
                '${_int(store['cheaper'])} (${_percentOf(_int(store['cheaper']), _int(store['offers']))})',
                _typicalLabel(toNum(store['median_diff'])),
              ],
              lastColor: _diffColor(toNum(store['median_diff']), colorScheme),
            ),
        ],
      ),
    );
  }

  Widget _segmentsPanel(ColorScheme colorScheme) {
    final rows = _list(_data[_showBrands ? 'brands' : 'categories']);

    return _panel(
      colorScheme,
      key: const Key('segments_panel'),
      title: 'Kategorie i marki',
      subtitle: 'Top 10: najpierw tam, gdzie konkurencja najczęściej jest tańsza',
      trailing: SegmentedButton<bool>(
        key: const Key('segments_toggle'),
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(value: false, label: Text('Kategorie')),
          ButtonSegment(value: true, label: Text('Marki')),
        ],
        selected: {_showBrands},
        onSelectionChanged: (selection) => setState(() => _showBrands = selection.first),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _tableRow(
            colorScheme,
            [_showBrands ? 'Marka' : 'Kategoria', 'Warianty z ofertą', 'Konkurencja taniej', 'Typowa różnica'],
            header: true,
          ),
          for (final row in rows)
            _tableRow(
              colorScheme,
              [
                row['name']?.toString() ?? '-',
                '${_int(row['products'])}',
                '${_int(row['competitor_cheaper'])} z ${_int(row['products'])}',
                _typicalLabel(toNum(row['median_diff'])),
              ],
              lastColor: _diffColor(toNum(row['median_diff']), colorScheme),
            ),
        ],
      ),
    );
  }

  Widget _gapsPanel(
    ColorScheme colorScheme, {
    required Key key,
    required String title,
    required String subtitle,
    required List<Map<String, dynamic>> items,
  }) {
    return _panel(
      colorScheme,
      key: key,
      title: title,
      subtitle: subtitle,
      child: items.isEmpty
          ? Text('Brak takich ofert.', style: TextStyle(color: colorScheme.onSurfaceVariant))
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final item in items)
                  InkWell(
                    borderRadius: BorderRadius.circular(10),
                    onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => ProductDetailsScreen(productId: _int(item['product_id'])),
                    )),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
                      child: Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  item['name']?.toString() ?? 'Produkt',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontWeight: FontWeight.w700),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  '${item['store'] ?? '-'}: ${price(toNum(item['theirs']))} · Twoja: ${price(toNum(item['ours']))}',
                                  style: TextStyle(fontSize: 12, color: colorScheme.onSurfaceVariant),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 12),
                          Text(
                            differenceLabel(toNum(item['diff']) ?? 0),
                            style: TextStyle(
                              color: _diffColor(toNum(item['diff']), colorScheme),
                              fontWeight: FontWeight.w800,
                              fontSize: 13,
                            ),
                          ),
                          Icon(Icons.chevron_right, size: 18, color: colorScheme.outlineVariant),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
    );
  }
}
