import 'package:flutter/material.dart';

num? toNum(dynamic value) {
  if (value == null) return null;
  if (value is num) return value;
  return num.tryParse(value.toString());
}

String signed(num value, String suffix) {
  final sign = value > 0 ? '+' : '';
  return "$sign${value.toStringAsFixed(suffix == '%' ? 1 : 2)}$suffix";
}

String price(num? value) => value != null ? "${value.toStringAsFixed(2)} zł" : "-";

/// Różnica oferty słowami (cena konkurencji - cena klienta): "164.00 zł drożej", "4.50 zł taniej".
String differenceLabel(num difference) {
  if (difference == 0) return 'ta sama cena';
  return "${difference.abs().toStringAsFixed(2)} zł ${difference > 0 ? 'drożej' : 'taniej'}";
}

/// Zakres różnic wariantów kafelka słowami, np. "Konkurencja taniej o 4.00–12.50 zł".
String groupDifferenceLabel(num minDiff, num maxDiff) {
  String amount(num value) => value.abs().toStringAsFixed(2);
  String range(num near, num far) => amount(near) == amount(far) ? amount(far) : "${amount(near)}–${amount(far)}";

  if (minDiff == 0 && maxDiff == 0) return 'Konkurencja: ta sama cena';
  if (maxDiff <= 0) return "Konkurencja taniej o ${range(maxDiff, minDiff)} zł";
  if (minDiff >= 0) return "Konkurencja drożej o ${range(minDiff, maxDiff)} zł";
  return "Konkurencja: od ${amount(minDiff)} zł taniej do ${amount(maxDiff)} zł drożej";
}

/// "1 wariant", "3 warianty", "5 wariantów".
String variantsLabel(int count) {
  final mod10 = count % 10;
  final mod100 = count % 100;
  if (count == 1) return '1 wariant';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return '$count warianty';
  return '$count wariantów';
}

String productsLabel(int count) {
  final mod10 = count % 10;
  final mod100 = count % 100;
  if (count == 1) return '1 produkt';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return '$count produkty';
  return '$count produktów';
}

Widget _thumbnail(String imageUrl, ColorScheme colorScheme) {
  return Container(
    width: 64,
    height: 64,
    decoration: BoxDecoration(
      color: colorScheme.surfaceContainerHighest.withOpacity(0.3),
      borderRadius: BorderRadius.circular(8),
    ),
    child: imageUrl.isNotEmpty
        ? ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Image.network(
              imageUrl,
              fit: BoxFit.cover,
              errorBuilder: (context, error, stackTrace) => const Icon(Icons.image_not_supported, size: 24),
            ),
          )
        : const Icon(Icons.inventory, size: 24),
  );
}

/// Jeden wariant produktu klienta (rozmiar/kolor) z cenami konkurencji.
class ProductRow extends StatelessWidget {
  final dynamic product;
  final VoidCallback onTap;

  const ProductRow({super.key, required this.product, required this.onTap});

  List<Widget> _competitorPrices(ColorScheme colorScheme) {
    final prices = product['competitor_prices'];
    if (prices is! List || prices.isEmpty) return [];

    return prices.map<Widget>((entry) {
      final num? competitorPrice = toNum(entry['price']);
      final num? difference = toNum(entry['difference']);
      final Color color = difference == null || difference == 0
          ? colorScheme.onSurfaceVariant
          : (difference > 0 ? Colors.green.shade700 : colorScheme.error);

      return Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(
          "${entry['store']}: ${price(competitorPrice)}${difference != null ? ' (${differenceLabel(difference)})' : ''}",
          style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600),
        ),
      );
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final String name = product['name']?.toString() ?? 'Brak nazwy';
    final String sku = product['sku']?.toString() ?? 'N/A';
    final String size = product['size']?.toString() ?? '';
    final String color = product['color']?.toString() ?? '';
    final num? displayPrice = toNum(
      product['display_price'] ?? product['price'] ?? product['price_special'] ?? product['price_normal'],
    );
    final int competitorsCount = int.tryParse('${product['competitors_count'] ?? 0}') ?? 0;
    final details = [
      "SKU: $sku",
      if (size.isNotEmpty) "Rozmiar: $size",
      if (color.isNotEmpty) "Kolor: $color",
      "Kat: ${product['category'] ?? '-'}",
      "Marka: ${product['manufacturer'] ?? '-'}",
    ].join(' | ');

    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 16.0, horizontal: 8.0),
        child: Row(
          children: [
            _thumbnail(product['image']?.toString() ?? '', colorScheme),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(name, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15), maxLines: 2, overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 4),
                  Text(details, style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12)),
                ],
              ),
            ),
            const SizedBox(width: 16),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  price(displayPrice),
                  style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: colorScheme.primary),
                ),
                ..._competitorPrices(colorScheme),
                if (competitorsCount > 0) ...[
                  const SizedBox(height: 6),
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.compare_arrows, size: 14, color: colorScheme.secondary),
                      const SizedBox(width: 4),
                      Text(
                        '$competitorsCount dopasowań',
                        style: TextStyle(color: colorScheme.secondary, fontSize: 12, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                ],
              ],
            ),
            const SizedBox(width: 16),
            Icon(Icons.chevron_right, color: colorScheme.outlineVariant, size: 20),
          ],
        ),
      ),
    );
  }
}

/// Kafelek produktu z liczbą wariantów spełniających filtry.
class ProductGroupTile extends StatelessWidget {
  final dynamic group;
  final bool filtersActive;
  final VoidCallback onTap;

  const ProductGroupTile({super.key, required this.group, required this.filtersActive, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final int matching = int.tryParse('${group['variants_matching'] ?? 0}') ?? 0;
    final int total = int.tryParse('${group['variants_total'] ?? matching}') ?? matching;
    final num? minPrice = toNum(group['min_price']);
    final num? maxPrice = toNum(group['max_price']);
    final num? minDiff = toNum(group['min_diff']);
    final num? maxDiff = toNum(group['max_diff']);

    final String prices = minPrice != null && maxPrice != null && minPrice != maxPrice
        ? "${minPrice.toStringAsFixed(2)} – ${price(maxPrice)}"
        : price(minPrice ?? maxPrice);
    final String variants = filtersActive ? "Filtry spełnia: $matching z ${variantsLabel(total)}" : variantsLabel(total);

    String? differences;
    Color differenceColor = colorScheme.onSurfaceVariant;
    if (minDiff != null && maxDiff != null) {
      differences = groupDifferenceLabel(minDiff, maxDiff);
      if (minDiff > 0) differenceColor = Colors.green.shade700;
      if (maxDiff < 0) differenceColor = colorScheme.error;
    }

    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 16.0, horizontal: 8.0),
        child: Row(
          children: [
            _thumbnail(group['image']?.toString() ?? '', colorScheme),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(group['name']?.toString() ?? 'Brak nazwy',
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15), maxLines: 2, overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 4),
                  Text("Kat: ${group['category'] ?? '-'} | Marka: ${group['manufacturer'] ?? '-'}",
                      style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12)),
                  const SizedBox(height: 4),
                  Text(variants, style: TextStyle(color: colorScheme.secondary, fontSize: 12, fontWeight: FontWeight.bold)),
                ],
              ),
            ),
            const SizedBox(width: 16),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(prices, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: colorScheme.primary)),
                if (differences != null) ...[
                  const SizedBox(height: 4),
                  Text(differences, style: TextStyle(color: differenceColor, fontSize: 12, fontWeight: FontWeight.w600)),
                ],
              ],
            ),
            const SizedBox(width: 16),
            Icon(Icons.chevron_right, color: colorScheme.outlineVariant, size: 20),
          ],
        ),
      ),
    );
  }
}
