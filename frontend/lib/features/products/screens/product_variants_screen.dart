import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';

import '../widgets/product_tiles.dart';
import 'product_details_screen.dart';

/// Warianty jednego produktu z zakładki dopasowanych.
class ProductVariantsScreen extends StatefulWidget {
  final String title;
  final String groupName;
  final String groupMaker;

  final String filterQuery;
  final int variantsMatching;
  final int variantsTotal;

  const ProductVariantsScreen({
    super.key,
    required this.title,
    required this.groupName,
    required this.groupMaker,
    required this.filterQuery,
    required this.variantsMatching,
    required this.variantsTotal,
  });

  @override
  State<ProductVariantsScreen> createState() => _ProductVariantsScreenState();
}

class _ProductVariantsScreenState extends State<ProductVariantsScreen> {
  List<dynamic> _variants = [];
  bool _isLoading = true;
  bool _showAll = false;

  bool get _hasFilters => widget.filterQuery.isNotEmpty && widget.variantsMatching < widget.variantsTotal;

  @override
  void initState() {
    super.initState();
    _fetchVariants();
  }

  Future<void> _fetchVariants() async {
    setState(() => _isLoading = true);
    try {
      var url = "/api/products?matched_only=true&per_page=100&sort=price&order=asc"
          "&group_name=${Uri.encodeQueryComponent(widget.groupName)}"
          "&group_maker=${Uri.encodeQueryComponent(widget.groupMaker)}";
      if (!_showAll) url += widget.filterQuery;

      final response = await ApiClient.get(Uri.parse(url), headers: {'Accept': 'application/json'});
      final data = jsonDecode(response.body);
      if (mounted) {
        setState(() => _variants = response.statusCode == 200 && data['ok'] == true ? (data['data'] ?? []) : []);
      }
    } catch (e) {
      debugPrint("Błąd pobierania wariantów: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final summary = _hasFilters && !_showAll
        ? "Filtry spełnia: ${widget.variantsMatching} z ${variantsLabel(widget.variantsTotal)}"
        : variantsLabel(_showAll ? widget.variantsTotal : _variants.length);

    return Scaffold(
      appBar: AppBar(title: Text(widget.title, overflow: TextOverflow.ellipsis)),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(child: Text(summary, style: TextStyle(color: colorScheme.secondary, fontWeight: FontWeight.bold))),
                if (_hasFilters)
                  TextButton(
                    key: const Key('show_all_variants'),
                    onPressed: () {
                      setState(() => _showAll = !_showAll);
                      _fetchVariants();
                    },
                    child: Text(_showAll ? 'Tylko spełniające filtry' : 'Pokaż wszystkie warianty'),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            Expanded(
              child: _isLoading
                  ? const Center(child: CircularProgressIndicator())
                  : _variants.isEmpty
                      ? const Center(child: Text("Brak wariantów spełniających kryteria."))
                      : ListView.separated(
                          itemCount: _variants.length,
                          separatorBuilder: (context, index) => Divider(height: 1, color: colorScheme.outlineVariant.withOpacity(0.3)),
                          itemBuilder: (context, index) {
                            final variant = _variants[index];
                            return ProductRow(
                              product: variant,
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(builder: (context) => ProductDetailsScreen(productId: variant['id'])),
                              ),
                            );
                          },
                        ),
            ),
          ],
        ),
      ),
    );
  }
}
