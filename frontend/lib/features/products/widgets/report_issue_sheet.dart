// Wysuwany panel "Zgłoś błąd" na widoku produktu: gotowe powody do kliknięcia albo własny opis, trafia do logów admina.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';

enum ReportTopic { comparison, recommendation, product, other }

class _TopicInfo {
  final String key;
  final String label;
  final IconData icon;
  final Map<String, String> reasons;

  const _TopicInfo(this.key, this.label, this.icon, this.reasons);
}

// Klucze powodów muszą zgadzać się z REPORT_TOPICS w webapp/routes/reports.py.
const Map<ReportTopic, _TopicInfo> _topics = {
  ReportTopic.comparison: _TopicInfo('comparison', 'Porównanie z konkurencją', Icons.compare_arrows, {
    'wrong_match': 'To inny produkt (złe dopasowanie)',
    'wrong_variant': 'Inny rozmiar lub wariant',
    'wrong_competitor_price': 'Nieprawidłowa cena konkurencji',
    'offer_unavailable': 'Oferta nieaktualna lub niedostępna',
    'wrong_difference': 'Źle policzona różnica ceny',
  }),
  ReportTopic.recommendation: _TopicInfo('recommendation', 'Rekomendacja ceny', Icons.auto_awesome, {
    'price_nonsense': 'Sugerowana cena nie ma sensu',
    'reason_mismatch': 'Uzasadnienie nie zgadza się z ceną',
    'reason_unclear': 'Uzasadnienie jest niezrozumiałe',
    'not_generated': 'Rekomendacja się nie generuje',
  }),
  ReportTopic.product: _TopicInfo('product', 'Dane mojego produktu', Icons.inventory_2_outlined, {
    'wrong_own_price': 'Zła cena mojego produktu',
    'wrong_name': 'Zła nazwa lub opis',
    'wrong_category': 'Zła kategoria lub marka',
    'wrong_image': 'Złe lub brakujące zdjęcie',
  }),
  ReportTopic.other: _TopicInfo('other', 'Inne', Icons.more_horiz, {}),
};

class ReportOffer {
  final String store;
  final String? sku;
  final String? name;
  final String? url;
  final num? price;

  const ReportOffer({required this.store, this.sku, this.name, this.url, this.price});

  String get label => price != null ? '$store · ${price!.toStringAsFixed(2)} zł' : store;

  Map<String, dynamic> toJson() => {'store': store, 'sku': sku, 'name': name, 'url': url, 'price': price};
}

class ReportRecommendation {
  final num price;
  final String reason;

  const ReportRecommendation({required this.price, required this.reason});
}

Future<void> showReportIssueSheet(
  BuildContext context, {
  required int productId,
  required String productName,
  List<ReportOffer> offers = const [],
  ReportRecommendation? recommendation,
  ReportTopic? topic,
  int? offerIndex,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    constraints: const BoxConstraints(maxWidth: 640),
    builder: (_) => ReportIssueSheet(
      productId: productId,
      productName: productName,
      offers: offers,
      recommendation: recommendation,
      initialTopic: topic,
      initialOfferIndex: offerIndex,
    ),
  );
}

class ReportIssueSheet extends StatefulWidget {
  final int productId;
  final String productName;
  final List<ReportOffer> offers;
  final ReportRecommendation? recommendation;
  final ReportTopic? initialTopic;
  final int? initialOfferIndex;

  const ReportIssueSheet({
    super.key,
    required this.productId,
    required this.productName,
    this.offers = const [],
    this.recommendation,
    this.initialTopic,
    this.initialOfferIndex,
  });

  @override
  State<ReportIssueSheet> createState() => _ReportIssueSheetState();
}

class _ReportIssueSheetState extends State<ReportIssueSheet> {
  late ReportTopic? _topic = widget.initialTopic;
  late int? _offerIndex = widget.initialOfferIndex;
  final Set<String> _reasons = {};
  final TextEditingController _comment = TextEditingController();
  bool _sending = false;
  String? _error;

  @override
  void dispose() {
    _comment.dispose();
    super.dispose();
  }

  bool get _canSend =>
      !_sending && _topic != null && (_reasons.isNotEmpty || _comment.text.trim().isNotEmpty);

  Future<void> _send() async {
    final topic = _topic;
    if (topic == null) return;

    setState(() {
      _sending = true;
      _error = null;
    });

    final body = <String, dynamic>{
      'topic': _topics[topic]!.key,
      'reasons': _reasons.toList(),
      'comment': _comment.text.trim(),
      if (topic == ReportTopic.comparison && _offerIndex != null) 'offer': widget.offers[_offerIndex!].toJson(),
      if (topic == ReportTopic.recommendation && widget.recommendation != null)
        'recommendation': {'price': widget.recommendation!.price, 'reason': widget.recommendation!.reason},
    };

    try {
      final response = await ApiClient.post(
        Uri.parse('/api/products/${widget.productId}/report'),
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        body: jsonEncode(body),
      );
      final data = jsonDecode(response.body);

      if (response.statusCode == 200 && data['ok'] == true) {
        if (!mounted) return;
        final messenger = ScaffoldMessenger.of(context);
        Navigator.of(context).pop();
        messenger.showSnackBar(const SnackBar(
          content: Text('Dziękujemy! Zgłoszenie trafiło do administratora.'),
          backgroundColor: Colors.green,
        ));
        return;
      }
      setState(() => _error = data['error']?.toString() ?? 'Nie udało się wysłać zgłoszenia.');
    } catch (e) {
      setState(() => _error = 'Nie udało się wysłać zgłoszenia. Spróbuj ponownie.');
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Widget _sectionLabel(String text) => Padding(
        padding: const EdgeInsets.only(top: 20, bottom: 8),
        child: Text(text, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
      );

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final topic = _topic;
    final reasons = topic != null ? _topics[topic]!.reasons : const <String, String>{};

    return Padding(
      padding: EdgeInsets.fromLTRB(24, 0, 24, 24 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Zgłoś błąd', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(widget.productName, style: TextStyle(color: colorScheme.onSurfaceVariant)),

            _sectionLabel('Czego dotyczy?'),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _topics.entries
                  .map((entry) => ChoiceChip(
                        key: Key('report_topic_${entry.value.key}'),
                        avatar: Icon(entry.value.icon, size: 16),
                        label: Text(entry.value.label),
                        selected: topic == entry.key,
                        onSelected: (_) => setState(() {
                          _topic = entry.key;
                          _reasons.clear();
                        }),
                      ))
                  .toList(),
            ),

            if (topic == ReportTopic.comparison && widget.offers.isNotEmpty) ...[
              _sectionLabel('Której oferty?'),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  ChoiceChip(
                    label: const Text('Wszystkich / nie wiem'),
                    selected: _offerIndex == null,
                    onSelected: (_) => setState(() => _offerIndex = null),
                  ),
                  for (var i = 0; i < widget.offers.length; i++)
                    ChoiceChip(
                      key: Key('report_offer_$i'),
                      label: Text(widget.offers[i].label),
                      selected: _offerIndex == i,
                      onSelected: (_) => setState(() => _offerIndex = i),
                    ),
                ],
              ),
            ],

            if (reasons.isNotEmpty) ...[
              _sectionLabel('Co jest nie tak?'),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: reasons.entries
                    .map((entry) => FilterChip(
                          key: Key('report_reason_${entry.key}'),
                          label: Text(entry.value),
                          selected: _reasons.contains(entry.key),
                          onSelected: (selected) => setState(() {
                            selected ? _reasons.add(entry.key) : _reasons.remove(entry.key);
                          }),
                        ))
                    .toList(),
              ),
            ],

            if (topic != null) ...[
              const SizedBox(height: 20),
              TextField(
                key: const Key('report_comment'),
                controller: _comment,
                maxLines: 3,
                maxLength: 1000,
                onChanged: (_) => setState(() {}),
                decoration: InputDecoration(
                  labelText: topic == ReportTopic.other ? 'Opisz, co się stało' : 'Dodatkowy opis (opcjonalnie)',
                  hintText: 'Np. u konkurencji to inny kolor, cena na stronie to 499 zł…',
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                ),
              ),
            ],

            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(_error!, style: TextStyle(color: colorScheme.error)),
            ],

            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: _sending ? null : () => Navigator.of(context).pop(),
                  child: const Text('Anuluj'),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  key: const Key('report_send'),
                  onPressed: _canSend ? _send : null,
                  icon: _sending
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.send, size: 18),
                  label: const Text('Wyślij zgłoszenie'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
