import 'package:flutter/material.dart';
import 'dart:convert';
import 'product_details_screen.dart';
import 'product_variants_screen.dart';
import '../widgets/product_tiles.dart';
import 'package:tonten/core/api/api_client.dart';

class _SortOption {
  final String label;
  final String? sort;
  final String order;
  const _SortOption(this.label, this.sort, this.order);
}

const Map<String, _SortOption> _matchedSorts = {
  'default': _SortOption('Sortuj: domyślnie', null, 'asc'),
  'diff_pln_desc': _SortOption('Największa różnica (zł)', 'diff_pln', 'desc'),
  'diff_percent_desc': _SortOption('Największa różnica (%)', 'diff_percent', 'desc'),
  'diff_pln_asc': _SortOption('Najmniejsza różnica (zł)', 'diff_pln', 'asc'),
  'diff_percent_asc': _SortOption('Najmniejsza różnica (%)', 'diff_percent', 'asc'),
  'name_asc': _SortOption('Nazwa A–Z', 'name', 'asc'),
  'name_desc': _SortOption('Nazwa Z–A', 'name', 'desc'),
  'price_asc': _SortOption('Cena rosnąco', 'price', 'asc'),
  'price_desc': _SortOption('Cena malejąco', 'price', 'desc'),
};
const Map<String, _SortOption> _allSorts = {
  'default': _SortOption('Sortuj: domyślnie', null, 'asc'),
  'name_asc': _SortOption('Nazwa A–Z', 'name', 'asc'),
  'name_desc': _SortOption('Nazwa Z–A', 'name', 'desc'),
  'price_asc': _SortOption('Cena rosnąco', 'price', 'asc'),
  'price_desc': _SortOption('Cena malejąco', 'price', 'desc'),
};

class ProductsListScreen extends StatefulWidget {
  final bool matchedOnly;

  const ProductsListScreen({super.key, required this.matchedOnly});

  @override
  State<ProductsListScreen> createState() => _ProductsListScreenState();
}

class _ProductsListScreenState extends State<ProductsListScreen> {
  List<dynamic> _products = [];
  bool _isLoading = true;
  int _currentPage = 1;
  int _totalPages = 1;

  String _searchQuery = "";
  final TextEditingController _searchController = TextEditingController();

  String? _selectedCategory;
  String? _selectedBrand;
  String? _selectedStore;
  int? _minMatches;
  final TextEditingController _minPriceController = TextEditingController();
  final TextEditingController _maxPriceController = TextEditingController();

  String? _priceDiff;
  String _diffUnit = 'pln';
  String _sortKey = 'default';

  Map<String, _SortOption> get _sortOptions => widget.matchedOnly ? _matchedSorts : _allSorts;
  final TextEditingController _diffMinController = TextEditingController();
  final TextEditingController _diffMaxController = TextEditingController();

  List<String> _availableCategories = [];
  List<String> _availableBrands = [];
  List<String> _availableStores = [];
  int _maxStoresScraped = 5;

  TabController? _tabController;

  @override
  void initState() {
    super.initState();
    _fetchFiltersData();
    _fetchProducts();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final tabController = DefaultTabController.maybeOf(context);
    if (_tabController != tabController) {
      _tabController?.removeListener(_onTabChanged);
      _tabController = tabController;
      _tabController?.addListener(_onTabChanged);
    }
  }

  void _onTabChanged() {
    if (_tabController != null && !_tabController!.indexIsChanging) {
      _fetchProducts();
    }
  }

  @override
  void dispose() {
    _tabController?.removeListener(_onTabChanged);
    _searchController.dispose();
    _minPriceController.dispose();
    _maxPriceController.dispose();
    _diffMinController.dispose();
    _diffMaxController.dispose();
    super.dispose();
  }

  Future<void> _fetchFiltersData() async {
    try {
      final response = await ApiClient.get(Uri.parse('/api/filters'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['ok'] == true && mounted) {
          setState(() {
            _availableCategories = List<String>.from(data['categories'] ?? []);
            _availableBrands = List<String>.from(data['brands'] ?? []);
            _availableStores = List<String>.from(data['stores'] ?? []);
            _maxStoresScraped = data['max_stores'] ?? 5;
          });
        }
      }
    } catch (e) {
      debugPrint("Błąd pobierania filtrów: $e");
    }
  }

  Future<void> _fetchProducts() async {
    if (!mounted) return;
    setState(() => _isLoading = true);

    try {
      final encodedSearch = Uri.encodeQueryComponent(_searchQuery);
      String urlStr = "/api/products?page=$_currentPage&per_page=20&matched_only=${widget.matchedOnly}&search=$encodedSearch";

      if (_selectedCategory != null && _selectedCategory!.isNotEmpty) {
        urlStr += "&category=${Uri.encodeQueryComponent(_selectedCategory!)}";
      }
      if (_selectedBrand != null && _selectedBrand!.isNotEmpty) {
        urlStr += "&brand=${Uri.encodeQueryComponent(_selectedBrand!)}";
      }
      urlStr += _variantFilterQuery();
      if (widget.matchedOnly) {
        urlStr += "&group=true";
      }
      final sortOption = _sortOptions[_sortKey];
      if (sortOption?.sort != null) {
        urlStr += "&sort=${sortOption!.sort}&order=${sortOption.order}";
      }

      final response = await ApiClient.get(Uri.parse(urlStr), headers: {'Accept': 'application/json'});

      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['ok'] == true) {
        if (mounted) {
          setState(() {
            _products = data['data'] ?? [];
            _totalPages = data['pagination']?['total_pages'] ?? 1;
          });
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Błąd pobierania: ${data['error'] ?? 'Nieznany'}'), backgroundColor: Colors.red),
          );
        }
      }
    } catch (e) {
      debugPrint("Błąd pobierania produktów: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  void _onSearch() {
    setState(() => _currentPage = 1);
    _fetchProducts();
  }

  void _clearFilters() {
    setState(() {
      _selectedCategory = null;
      _selectedBrand = null;
      _selectedStore = null;
      _minMatches = null;
      _minPriceController.clear();
      _maxPriceController.clear();
      _priceDiff = null;
      _diffUnit = 'pln';
      _diffMinController.clear();
      _diffMaxController.clear();
      _sortKey = 'default';
      _searchController.clear();
      _searchQuery = "";
      _currentPage = 1;
    });
    _fetchProducts();
  }

  /// Filtry pojedynczych wariantów, używane też przez listę wariantów kafelka.
  String _variantFilterQuery() {
    var query = '';
    if (_selectedStore != null && _selectedStore!.isNotEmpty) {
      query += "&store=${Uri.encodeQueryComponent(_selectedStore!)}";
    }
    if (_minMatches != null) {
      query += "&min_matches=$_minMatches";
    }
    if (_minPriceController.text.isNotEmpty) {
      query += "&price_min=${Uri.encodeQueryComponent(_minPriceController.text)}";
    }
    if (_maxPriceController.text.isNotEmpty) {
      query += "&price_max=${Uri.encodeQueryComponent(_maxPriceController.text)}";
    }
    if (widget.matchedOnly) {
      query += _priceDiffQuery();
    }
    return query;
  }

  void _openGroup(dynamic group) {
    final int matching = int.tryParse('${group['variants_matching'] ?? 0}') ?? 0;
    final int total = int.tryParse('${group['variants_total'] ?? matching}') ?? matching;
    final Widget screen = matching == 1 && total == 1
        ? ProductDetailsScreen(productId: group['first_id'])
        : ProductVariantsScreen(
            title: group['name']?.toString() ?? 'Warianty',
            groupName: group['group_name']?.toString() ?? '',
            groupMaker: group['group_maker']?.toString() ?? '',
            filterQuery: _variantFilterQuery(),
            variantsMatching: matching,
            variantsTotal: total,
          );
    Navigator.push(context, MaterialPageRoute(builder: (context) => screen)).then((_) => _fetchProducts());
  }

  String _priceDiffQuery() {
    final diffMin = _diffMinController.text.trim().replaceAll(',', '.');
    final diffMax = _diffMaxController.text.trim().replaceAll(',', '.');
    var query = '';

    if (_priceDiff != null) {
      query += "&price_diff=$_priceDiff";
    }
    if (diffMin.isNotEmpty) {
      query += "&diff_min=${Uri.encodeQueryComponent(diffMin)}";
    }
    if (diffMax.isNotEmpty) {
      query += "&diff_max=${Uri.encodeQueryComponent(diffMax)}";
    }
    if (diffMin.isNotEmpty || diffMax.isNotEmpty) {
      query += "&diff_unit=$_diffUnit";
    }
    return query;
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _searchController,
                decoration: InputDecoration(
                  hintText: 'Wpisz nazwę produktu lub SKU...',
                  prefixIcon: const Icon(Icons.search),
                  filled: true,
                  fillColor: colorScheme.surface,
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                ),
                onSubmitted: (val) {
                  _searchQuery = val;
                  _onSearch();
                },
              ),
            ),
            const SizedBox(width: 16),
            ElevatedButton.icon(
              onPressed: () {
                _searchQuery = _searchController.text;
                _onSearch();
              },
              style: ElevatedButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              icon: const Icon(Icons.search, size: 18),
              label: const Text("Szukaj", style: TextStyle(fontWeight: FontWeight.bold)),
            ),
          ],
        ),
        const SizedBox(height: 16),

        Wrap(
          spacing: 12,
          runSpacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            _buildDropdown('Marka', _selectedBrand, _availableBrands, (val) {
              setState(() { _selectedBrand = val; _currentPage = 1; });
              _fetchProducts();
            }, colorScheme),

            _buildDropdown('Kategoria', _selectedCategory, _availableCategories, (val) {
              setState(() { _selectedCategory = val; _currentPage = 1; });
              _fetchProducts();
            }, colorScheme),

            _buildDropdown('Sklep konkurencji', _selectedStore, _availableStores, (val) {
              setState(() { _selectedStore = val; _currentPage = 1; });
              _fetchProducts();
            }, colorScheme),

            if (widget.matchedOnly)
              _buildMatchesDropdown(colorScheme),

            _buildPriceRange(colorScheme),

            if (widget.matchedOnly)
              _buildPriceDiffFilter(colorScheme),

            _buildSortDropdown(colorScheme),

            TextButton.icon(
              onPressed: _clearFilters,
              icon: const Icon(Icons.clear, size: 16),
              label: const Text("Wyczyść", style: TextStyle(fontSize: 13)),
              style: TextButton.styleFrom(foregroundColor: colorScheme.onSurfaceVariant),
            ),
          ],
        ),
        const SizedBox(height: 24),

        Expanded(
          child: _isLoading
              ? const Center(child: CircularProgressIndicator())
              : _products.isEmpty
                  ? const Center(child: Text("Brak produktów spełniających kryteria."))
                  : ListView.separated(
                      itemCount: _products.length,
                      separatorBuilder: (context, index) => Divider(height: 1, color: colorScheme.outlineVariant.withOpacity(0.3)),
                      itemBuilder: (context, index) {
                        final item = _products[index];
                        if (widget.matchedOnly) {
                          return ProductGroupTile(
                            group: item,
                            filtersActive: _variantFilterQuery().isNotEmpty,
                            onTap: () => _openGroup(item),
                          );
                        }
                        return ProductRow(
                          product: item,
                          onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (context) => ProductDetailsScreen(productId: item['id'], showPriceRecommendation: false),
                            ),
                          ).then((_) => _fetchProducts()),
                        );
                      },
                    ),
        ),

        if (!_isLoading && _totalPages > 1)
          Padding(
            padding: const EdgeInsets.only(top: 16.0),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                IconButton(
                  icon: const Icon(Icons.chevron_left),
                  onPressed: _currentPage > 1 ? () { setState(() => _currentPage--); _fetchProducts(); } : null,
                ),
                Text('Strona $_currentPage z $_totalPages', style: const TextStyle(fontWeight: FontWeight.bold)),
                IconButton(
                  icon: const Icon(Icons.chevron_right),
                  onPressed: _currentPage < _totalPages ? () { setState(() => _currentPage++); _fetchProducts(); } : null,
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _buildDropdown(String label, String? currentValue, List<String> options, Function(String?) onChanged, ColorScheme colorScheme) {
    return Container(
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: colorScheme.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.4)),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String?>(
          value: currentValue,
          icon: const Icon(Icons.arrow_drop_down, size: 18),
          hint: Text(label, style: const TextStyle(fontSize: 13)),
          items: [
            DropdownMenuItem(value: null, child: Text('$label: Wszystkie', style: const TextStyle(fontSize: 13))),
            ...options.map((e) => DropdownMenuItem(value: e, child: Text(e, style: const TextStyle(fontSize: 13)))),
          ],
          onChanged: onChanged,
        ),
      ),
    );
  }

  Widget _buildMatchesDropdown(ColorScheme colorScheme) {
    List<int> dynamicMatches = List.generate(_maxStoresScraped, (index) => index + 1);

    return Container(
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: colorScheme.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.4)),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int?>(
          value: _minMatches,
          icon: const Icon(Icons.filter_alt_outlined, size: 18),
          hint: const Text('Dopasowania: Wszystkie', style: TextStyle(fontSize: 13)),
          items: [
            const DropdownMenuItem(value: null, child: Text('Dopasowania: Wszystkie', style: TextStyle(fontSize: 13))),
            ...dynamicMatches.map((e) => DropdownMenuItem(
                  value: e,
                  child: Text('$e+ dopasowań ze sklepów', style: const TextStyle(fontSize: 13)),
                )),
          ],
          onChanged: (val) {
            setState(() { _minMatches = val; _currentPage = 1; });
            _fetchProducts();
          },
        ),
      ),
    );
  }

  Widget _buildPriceRange(ColorScheme colorScheme) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          width: 80,
          height: 38,
          child: TextField(
            controller: _minPriceController,
            keyboardType: TextInputType.number,
            style: const TextStyle(fontSize: 13),
            decoration: InputDecoration(
              hintText: 'Cena od',
              contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 0),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
            ),
            onSubmitted: (_) { setState(() => _currentPage = 1); _fetchProducts(); },
          ),
        ),
        const SizedBox(width: 8),
        const Text("-", style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(width: 8),
        SizedBox(
          width: 80,
          height: 38,
          child: TextField(
            controller: _maxPriceController,
            keyboardType: TextInputType.number,
            style: const TextStyle(fontSize: 13),
            decoration: InputDecoration(
              hintText: 'Cena do',
              contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 0),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
            ),
            onSubmitted: (_) { setState(() => _currentPage = 1); _fetchProducts(); },
          ),
        ),
      ],
    );
  }

  Widget _buildPriceDiffFilter(ColorScheme colorScheme) {
    void apply() {
      setState(() => _currentPage = 1);
      _fetchProducts();
    }

    Widget amountField(TextEditingController controller, String hint, Key key) {
      return SizedBox(
        width: 90,
        height: 38,
        child: TextField(
          key: key,
          controller: controller,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: const TextStyle(fontSize: 13),
          decoration: InputDecoration(
            hintText: hint,
            contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 0),
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
          ),
          onSubmitted: (_) => apply(),
        ),
      );
    }

    BoxDecoration boxDecoration() => BoxDecoration(
          color: colorScheme.surface,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.4)),
        );

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          height: 38,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: boxDecoration(),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String?>(
              key: const Key('price_diff_dropdown'),
              value: _priceDiff,
              icon: const Icon(Icons.swap_vert, size: 18),
              hint: const Text('Cena konkurencji: Wszystkie', style: TextStyle(fontSize: 13)),
              items: const [
                DropdownMenuItem(value: null, child: Text('Cena konkurencji: Wszystkie', style: TextStyle(fontSize: 13))),
                DropdownMenuItem(value: 'higher', child: Text('Konkurencja droższa', style: TextStyle(fontSize: 13))),
                DropdownMenuItem(value: 'lower', child: Text('Konkurencja tańsza', style: TextStyle(fontSize: 13))),
              ],
              onChanged: (val) {
                _priceDiff = val;
                apply();
              },
            ),
          ),
        ),
        const SizedBox(width: 8),
        amountField(_diffMinController, 'Różnica od', const Key('diff_min_field')),
        const SizedBox(width: 8),
        const Text("-", style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(width: 8),
        amountField(_diffMaxController, 'Różnica do', const Key('diff_max_field')),
        const SizedBox(width: 8),
        Container(
          height: 38,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: boxDecoration(),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('diff_unit_dropdown'),
              value: _diffUnit,
              items: const [
                DropdownMenuItem(value: 'pln', child: Text('zł', style: TextStyle(fontSize: 13))),
                DropdownMenuItem(value: 'percent', child: Text('%', style: TextStyle(fontSize: 13))),
              ],
              onChanged: (val) {
                _diffUnit = val ?? 'pln';
                if (_diffMinController.text.isNotEmpty || _diffMaxController.text.isNotEmpty) {
                  apply();
                } else {
                  setState(() {});
                }
              },
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSortDropdown(ColorScheme colorScheme) {
    return Container(
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: colorScheme.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.4)),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          key: const Key('sort_dropdown'),
          value: _sortOptions.containsKey(_sortKey) ? _sortKey : 'default',
          icon: const Icon(Icons.sort, size: 18),
          items: _sortOptions.entries
              .map((entry) => DropdownMenuItem(value: entry.key, child: Text(entry.value.label, style: const TextStyle(fontSize: 13))))
              .toList(),
          onChanged: (val) {
            setState(() {
              _sortKey = val ?? 'default';
              _currentPage = 1;
            });
            _fetchProducts();
          },
        ),
      ),
    );
  }
}
