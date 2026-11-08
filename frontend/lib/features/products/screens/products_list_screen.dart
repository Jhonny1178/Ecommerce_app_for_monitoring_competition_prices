import 'dart:async';
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
  'default': _SortOption('Domyślnie', null, 'asc'),
  'diff_pln_desc': _SortOption('Największa różnica ceny', 'diff_pln', 'desc'),
  'diff_pln_asc': _SortOption('Najmniejsza różnica ceny', 'diff_pln', 'asc'),
  'name_asc': _SortOption('Nazwa A–Z', 'name', 'asc'),
  'name_desc': _SortOption('Nazwa Z–A', 'name', 'desc'),
  'price_asc': _SortOption('Cena rosnąco', 'price', 'asc'),
  'price_desc': _SortOption('Cena malejąco', 'price', 'desc'),
};
const Map<String, _SortOption> _allSorts = {
  'default': _SortOption('Domyślnie', null, 'asc'),
  'name_asc': _SortOption('Nazwa A–Z', 'name', 'asc'),
  'name_desc': _SortOption('Nazwa Z–A', 'name', 'desc'),
  'price_asc': _SortOption('Cena rosnąco', 'price', 'asc'),
  'price_desc': _SortOption('Cena malejąco', 'price', 'desc'),
};

// Pola liczbowe filtrują same chwilę po skończeniu pisania (Enter działa od razu).
const Duration _typingDebounce = Duration(milliseconds: 700);
const Duration _stepperDebounce = Duration(milliseconds: 400);

class ProductsListScreen extends StatefulWidget {
  final bool matchedOnly;
  // Otwarcie z Dashboardu: od razu filtr podejrzanych dopasowań.
  final bool initialHighRisk;

  const ProductsListScreen({super.key, required this.matchedOnly, this.initialHighRisk = false});

  @override
  State<ProductsListScreen> createState() => _ProductsListScreenState();
}

class _ProductsListScreenState extends State<ProductsListScreen> {
  List<dynamic> _products = [];
  bool _isLoading = true;
  int _currentPage = 1;
  int _totalPages = 1;
  int _totalItems = 0;
  int _requestCounter = 0;
  Timer? _debounce;

  String _searchQuery = "";
  final TextEditingController _searchController = TextEditingController();

  String? _selectedCategory;
  String? _selectedBrand;
  String? _selectedStore;
  // 0 = dowolna liczba sklepów konkurencji z ofertą.
  int _minMatches = 0;
  final TextEditingController _minPriceController = TextEditingController();
  final TextEditingController _maxPriceController = TextEditingController();

  String? _priceDiff;
  // Różnica ceny ponad 50% (HIGH_RISK_DIFF_PCT w webapp/routes/products.py) = możliwe złe dopasowanie.
  late bool _highRisk = widget.initialHighRisk;
  String _sortKey = 'default';

  Map<String, _SortOption> get _sortOptions => widget.matchedOnly ? _matchedSorts : _allSorts;
  final TextEditingController _diffMinController = TextEditingController();
  final TextEditingController _diffMaxController = TextEditingController();

  List<String> _availableCategories = [];
  List<String> _availableBrands = [];
  List<String> _availableStores = [];

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
    _debounce?.cancel();
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
          });
        }
      }
    } catch (e) {
      debugPrint("Błąd pobierania filtrów: $e");
    }
  }

  Future<void> _fetchProducts() async {
    if (!mounted) return;
    _debounce?.cancel();
    // Odpowiedź starszego zapytania nie może nadpisać nowszej (szybkie zmiany filtrów).
    final requestId = ++_requestCounter;
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
      if (requestId != _requestCounter) return;

      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['ok'] == true) {
        if (mounted) {
          setState(() {
            _products = data['data'] ?? [];
            _totalPages = data['pagination']?['total_pages'] ?? 1;
            _totalItems = data['pagination']?['total_items'] ?? _products.length;
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
      if (mounted && requestId == _requestCounter) setState(() => _isLoading = false);
    }
  }

  void _onSearch() {
    setState(() => _currentPage = 1);
    _fetchProducts();
  }

  void _applyFilters() {
    _currentPage = 1;
    _fetchProducts();
  }

  void _applyFiltersLater([Duration delay = _typingDebounce]) {
    _debounce?.cancel();
    _debounce = Timer(delay, _applyFilters);
  }

  // Czyści tylko filtry; wyszukiwanie i sortowanie zostają.
  void _clearFilters() {
    setState(() {
      _selectedCategory = null;
      _selectedBrand = null;
      _selectedStore = null;
      _minMatches = 0;
      _minPriceController.clear();
      _maxPriceController.clear();
      _priceDiff = null;
      _highRisk = false;
      _diffMinController.clear();
      _diffMaxController.clear();
    });
    _applyFilters();
  }

  /// Liczba z pola (przecinek lub kropka) albo null, gdy pole jest puste lub wpis nie jest jeszcze liczbą.
  String? _numberParam(TextEditingController controller) {
    final text = controller.text.trim().replaceAll(',', '.');
    final value = double.tryParse(text);
    if (value == null || value < 0) return null;
    return text;
  }

  /// Filtry pojedynczych wariantów, używane też przez listę wariantów kafelka.
  String _variantFilterQuery() {
    var query = '';
    if (_selectedStore != null && _selectedStore!.isNotEmpty) {
      query += "&store=${Uri.encodeQueryComponent(_selectedStore!)}";
    }
    if (_minMatches > 0) {
      query += "&min_matches=$_minMatches";
    }
    final priceMin = _numberParam(_minPriceController);
    final priceMax = _numberParam(_maxPriceController);
    if (priceMin != null) {
      query += "&price_min=${Uri.encodeQueryComponent(priceMin)}";
    }
    if (priceMax != null) {
      query += "&price_max=${Uri.encodeQueryComponent(priceMax)}";
    }
    if (widget.matchedOnly) {
      query += _priceDiffQuery();
      if (_highRisk) query += "&risk=high";
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
    final diffMin = _numberParam(_diffMinController);
    final diffMax = _numberParam(_diffMaxController);
    var query = '';

    if (_priceDiff != null) {
      query += "&price_diff=$_priceDiff";
    }
    if (diffMin != null) {
      query += "&diff_min=${Uri.encodeQueryComponent(diffMin)}";
    }
    if (diffMax != null) {
      query += "&diff_max=${Uri.encodeQueryComponent(diffMax)}";
    }
    if (diffMin != null || diffMax != null) {
      query += "&diff_unit=pln";
    }
    return query;
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final chips = _activeFilterChips();

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
              setState(() => _selectedBrand = val);
              _applyFilters();
            }, colorScheme),
            _buildDropdown('Kategoria', _selectedCategory, _availableCategories, (val) {
              setState(() => _selectedCategory = val);
              _applyFilters();
            }, colorScheme),
            _buildDropdown('Sklep', _selectedStore, _availableStores, (val) {
              setState(() => _selectedStore = val);
              _applyFilters();
            }, colorScheme),
            if (widget.matchedOnly) _buildMinStoresStepper(colorScheme),
            if (widget.matchedOnly)
              Tooltip(
                message: 'Oferty, których cena różni się o ponad 50% — możliwe złe dopasowanie produktu.',
                child: FilterChip(
                  key: const Key('high_risk_filter'),
                  avatar: Icon(Icons.warning_amber_rounded, size: 16, color: colorScheme.error),
                  label: const Text('Podejrzane dopasowania', style: TextStyle(fontSize: 13)),
                  selected: _highRisk,
                  onSelected: (selected) {
                    setState(() => _highRisk = selected);
                    _applyFilters();
                  },
                ),
              ),
          ],
        ),
        const SizedBox(height: 12),
        _buildFilterRow('Twoja cena', _buildPriceRange(colorScheme)),
        if (widget.matchedOnly) ...[
          const SizedBox(height: 12),
          _buildFilterRow('Konkurencja', _buildPriceDiffFilter(colorScheme)),
        ],
        const SizedBox(height: 16),

        if (chips.isNotEmpty) ...[
          Wrap(
            spacing: 8,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              ...chips,
              TextButton.icon(
                key: const Key('clear_filters'),
                onPressed: _clearFilters,
                icon: const Icon(Icons.filter_alt_off_outlined, size: 16),
                label: const Text("Wyczyść filtry", style: TextStyle(fontSize: 13)),
                style: TextButton.styleFrom(foregroundColor: colorScheme.onSurfaceVariant),
              ),
            ],
          ),
          const SizedBox(height: 8),
        ],
        _buildResultsBar(colorScheme),
        const SizedBox(height: 8),

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

  BoxDecoration _boxDecoration(ColorScheme colorScheme, {bool active = false}) {
    return BoxDecoration(
      color: active ? colorScheme.primaryContainer.withOpacity(0.35) : colorScheme.surface,
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: active ? colorScheme.primary : colorScheme.outlineVariant.withOpacity(0.4)),
    );
  }

  TextStyle _controlTextStyle(ColorScheme colorScheme, {bool active = false}) {
    return TextStyle(
      fontSize: 13,
      fontWeight: active ? FontWeight.w600 : FontWeight.normal,
      color: active ? colorScheme.primary : colorScheme.onSurface,
    );
  }

  // Teksty na przycisku listy (wyróżnione, gdy filtr działa); pozycje w menu zostają zwykłe.
  List<Widget> _selectedLabels(List<String> labels, ColorScheme colorScheme, {bool active = false}) {
    return labels
        .map((label) => Align(
              alignment: Alignment.centerLeft,
              child: Text(label, style: _controlTextStyle(colorScheme, active: active)),
            ))
        .toList();
  }

  Widget _buildFilterRow(String label, Widget controls) {
    return Row(
      children: [
        SizedBox(
          width: 110,
          child: Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
        ),
        Flexible(child: controls),
      ],
    );
  }

  // Na przycisku zawsze "Nazwa: wartość", żeby po wyborze było widać, który to filtr.
  Widget _buildDropdown(String label, String? currentValue, List<String> options, ValueChanged<String?> onChanged, ColorScheme colorScheme) {
    final active = currentValue != null;
    final values = <String?>[null, ...options];

    return Container(
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: _boxDecoration(colorScheme, active: active),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String?>(
          value: currentValue,
          icon: const Icon(Icons.arrow_drop_down, size: 18),
          selectedItemBuilder: (context) => _selectedLabels(
            values.map((value) => '$label: ${value ?? 'wszystkie'}').toList(),
            colorScheme,
            active: active,
          ),
          items: values
              .map((value) => DropdownMenuItem<String?>(
                    value: value,
                    child: Text(value ?? 'Wszystkie', style: const TextStyle(fontSize: 13)),
                  ))
              .toList(),
          onChanged: onChanged,
        ),
      ),
    );
  }

  // Licznik − / + bez górnego limitu: działa też, gdy klient będzie miał więcej konkurentów.
  Widget _buildMinStoresStepper(ColorScheme colorScheme) {
    final active = _minMatches > 0;

    void change(int delta) {
      setState(() => _minMatches = (_minMatches + delta).clamp(0, 99));
      _applyFiltersLater(_stepperDebounce);
    }

    return Container(
      key: const Key('min_stores_stepper'),
      height: 38,
      padding: const EdgeInsets.only(left: 12),
      decoration: _boxDecoration(colorScheme, active: active),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Tooltip(
            message: 'Pokaż produkty, które mają ofertę w co najmniej tylu sklepach konkurencji.',
            child: Text(
              'Sklepy z ofertą: ${active ? 'min. $_minMatches' : 'dowolnie'}',
              key: const Key('min_stores_label'),
              style: _controlTextStyle(colorScheme, active: active),
            ),
          ),
          IconButton(
            key: const Key('min_stores_minus'),
            tooltip: 'Mniej',
            icon: const Icon(Icons.remove, size: 16),
            visualDensity: VisualDensity.compact,
            onPressed: active ? () => change(-1) : null,
          ),
          IconButton(
            key: const Key('min_stores_plus'),
            tooltip: 'Więcej',
            icon: const Icon(Icons.add, size: 16),
            visualDensity: VisualDensity.compact,
            onPressed: () => change(1),
          ),
        ],
      ),
    );
  }

  Widget _amountField(TextEditingController controller, String hint, Key key, {String? suffix}) {
    final colorScheme = Theme.of(context).colorScheme;
    final active = _numberParam(controller) != null;

    return SizedBox(
      width: 100,
      height: 38,
      child: TextField(
        key: key,
        controller: controller,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        style: const TextStyle(fontSize: 13),
        decoration: InputDecoration(
          hintText: hint,
          suffixText: suffix,
          filled: true,
          fillColor: active ? colorScheme.primaryContainer.withOpacity(0.35) : colorScheme.surface,
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 0),
          // Ta sama ramka co przy listach obok; podświetlona, gdy pole filtruje.
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: active ? colorScheme.primary : colorScheme.outlineVariant.withOpacity(0.4)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: colorScheme.primary, width: 1.5),
          ),
        ),
        onChanged: (_) => _applyFiltersLater(),
        onSubmitted: (_) => _applyFilters(),
      ),
    );
  }

  Widget _buildPriceRange(ColorScheme colorScheme) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        _amountField(_minPriceController, 'od', const Key('price_min_field'), suffix: 'zł'),
        const Text("–", style: TextStyle(fontWeight: FontWeight.bold)),
        _amountField(_maxPriceController, 'do', const Key('price_max_field'), suffix: 'zł'),
      ],
    );
  }

  // Czyta się jak zdanie: "Konkurencja tańsza o 10 – 50 zł" (różnica tylko w zł).
  Widget _buildPriceDiffFilter(ColorScheme colorScheme) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Container(
          height: 38,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: _boxDecoration(colorScheme, active: _priceDiff != null),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String?>(
              key: const Key('price_diff_dropdown'),
              value: _priceDiff,
              icon: const Icon(Icons.arrow_drop_down, size: 18),
              selectedItemBuilder: (context) => _selectedLabels(
                const ['tańsza lub droższa', 'tańsza', 'droższa'],
                colorScheme,
                active: _priceDiff != null,
              ),
              items: const [
                DropdownMenuItem(value: null, child: Text('tańsza lub droższa', style: TextStyle(fontSize: 13))),
                DropdownMenuItem(value: 'lower', child: Text('tańsza', style: TextStyle(fontSize: 13))),
                DropdownMenuItem(value: 'higher', child: Text('droższa', style: TextStyle(fontSize: 13))),
              ],
              onChanged: (val) {
                setState(() => _priceDiff = val);
                _applyFilters();
              },
            ),
          ),
        ),
        const Text("o", style: TextStyle(fontSize: 13)),
        _amountField(_diffMinController, 'od', const Key('diff_min_field'), suffix: 'zł'),
        const Text("–", style: TextStyle(fontWeight: FontWeight.bold)),
        _amountField(_diffMaxController, 'do', const Key('diff_max_field'), suffix: 'zł'),
      ],
    );
  }

  String? _rangeLabel(String? min, String? max, String unit) {
    String show(String value) => value.replaceAll('.', ',');
    if (min == null && max == null) return null;
    if (min != null && max != null) return '${show(min)}–${show(max)} $unit';
    return min != null ? 'od ${show(min)} $unit' : 'do ${show(max!)} $unit';
  }

  // Aktywne filtry jako znaczniki z ✕ nad listą wyników.
  List<Widget> _activeFilterChips() {
    final chips = <Widget>[];

    void add(String label, VoidCallback clear) {
      chips.add(InputChip(
        label: Text(label, style: const TextStyle(fontSize: 12)),
        visualDensity: VisualDensity.compact,
        deleteIcon: const Icon(Icons.close, size: 16),
        onDeleted: () {
          setState(clear);
          _applyFilters();
        },
      ));
    }

    if (_selectedBrand != null) add('Marka: $_selectedBrand', () => _selectedBrand = null);
    if (_selectedCategory != null) add('Kategoria: $_selectedCategory', () => _selectedCategory = null);
    if (_selectedStore != null) add('Sklep: $_selectedStore', () => _selectedStore = null);
    if (_minMatches > 0) add('Sklepy z ofertą: min. $_minMatches', () => _minMatches = 0);

    final price = _rangeLabel(_numberParam(_minPriceController), _numberParam(_maxPriceController), 'zł');
    if (price != null) {
      add('Twoja cena: $price', () {
        _minPriceController.clear();
        _maxPriceController.clear();
      });
    }

    if (widget.matchedOnly) {
      if (_highRisk) add('Podejrzane dopasowania (różnica ponad 50%)', () => _highRisk = false);
      if (_priceDiff != null) {
        add(_priceDiff == 'lower' ? 'Konkurencja tańsza' : 'Konkurencja droższa', () => _priceDiff = null);
      }
      final diff = _rangeLabel(_numberParam(_diffMinController), _numberParam(_diffMaxController), 'zł');
      if (diff != null) {
        add('Różnica: $diff', () {
          _diffMinController.clear();
          _diffMaxController.clear();
        });
      }
    }

    return chips;
  }

  Widget _buildResultsBar(ColorScheme colorScheme) {
    return Row(
      children: [
        Expanded(
          child: Text(
            _isLoading ? 'Wyszukiwanie…' : 'Znaleziono ${productsLabel(_totalItems)}',
            key: const Key('results_count'),
            style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: colorScheme.onSurfaceVariant),
          ),
        ),
        Text('Sortuj:', style: TextStyle(fontSize: 13, color: colorScheme.onSurfaceVariant)),
        const SizedBox(width: 8),
        _buildSortDropdown(colorScheme),
      ],
    );
  }

  Widget _buildSortDropdown(ColorScheme colorScheme) {
    final active = _sortKey != 'default';

    return Container(
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: _boxDecoration(colorScheme, active: active),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          key: const Key('sort_dropdown'),
          value: _sortOptions.containsKey(_sortKey) ? _sortKey : 'default',
          icon: const Icon(Icons.sort, size: 18),
          selectedItemBuilder: (context) => _selectedLabels(
            _sortOptions.values.map((option) => option.label).toList(),
            colorScheme,
            active: active,
          ),
          items: _sortOptions.entries
              .map((entry) => DropdownMenuItem(
                    value: entry.key,
                    child: Text(entry.value.label, style: const TextStyle(fontSize: 13)),
                  ))
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
