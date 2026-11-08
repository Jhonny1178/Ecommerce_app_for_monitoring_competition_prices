import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:tonten/core/api/api_client.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../auth/screens/subscription_screen.dart';
import '../widgets/report_issue_sheet.dart';

/// Adres produktu klienta bez dopisku feedu (".feed10000.html").
String clientProductUrl(String? url) => (url ?? '').replaceFirst(RegExp(r'\.feed\d+\.html$'), '.html');

class ProductDetailsScreen extends StatefulWidget {
  final int productId;

  final bool showPriceRecommendation;

  const ProductDetailsScreen({
    super.key,
    required this.productId,
    this.showPriceRecommendation = true,
  });

  @override
  State<ProductDetailsScreen> createState() => _ProductDetailsScreenState();
}

class _ProductDetailsScreenState extends State<ProductDetailsScreen> {
  bool _isLoading = true;
  Map<String, dynamic>? _product;
  List<dynamic> _competitors = [];

  bool _isHistoryLoading = false;
  List<dynamic> _priceHistory = [];
  String? _priceHistoryError;

  bool _isRecommending = false;
  double? _recommendedPrice;
  String? _recommendationReason;
  String _subscriptionPlan = 'Podstawowy';

  @override
  void initState() {
    super.initState();
    _fetchDetails();
  }

  bool get _hasPriceHistoryAccess {
    final plan = _subscriptionPlan.trim().toLowerCase();

    return plan == 'pro' ||
        plan == 'enterprise' ||
        plan == 'premium';
  }

  Future<void> _openPlans() async {
    final changed = await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => SubscriptionScreen(isChangingPlan: true, currentPlan: _subscriptionPlan),
      ),
    );
    if (changed == true) _fetchDetails();
  }

  Future<void> _launchCompetitorUrl(String? urlString) async {
    if (urlString == null || urlString.isEmpty) return;

    final Uri url = Uri.parse(urlString);

    if (!url.isScheme('http') && !url.isScheme('https')) return;

    if (await canLaunchUrl(url)) {
      await launchUrl(url, mode: LaunchMode.externalApplication);
    } else if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Nie można otworzyć tego linku'),
          backgroundColor: Colors.red,
        ),
      );
    }
  }

  Future<void> _fetchDetails() async {
    if (mounted) setState(() => _isLoading = true);

    try {
      final meResponse = await ApiClient.get(Uri.parse('/api/me'));
      if (meResponse.statusCode == 200) {
        final meData = jsonDecode(meResponse.body);
        if (meData['ok'] == true && meData['user'] != null) {
          _subscriptionPlan = meData['user']['subscription_plan'] ?? 'Basic';
        }
      }

      final url = Uri.parse("/api/products/${widget.productId}");
      final response = await ApiClient.get(
        url,
        headers: {'Accept': 'application/json'},
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);

        if (data['ok'] == true && mounted) {
          setState(() {
            _product = data['data'];
            _competitors = data['competitors'] ?? [];
          });

          if (_hasPriceHistoryAccess) {
            await _fetchPriceHistory();
          }
        }
      }
    } catch (e) {
      debugPrint("Błąd pobierania detali: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _fetchPriceHistory() async {
    if (mounted) {
      setState(() {
        _isHistoryLoading = true;
        _priceHistoryError = null;
      });
    }

    try {
      final response = await ApiClient.get(
        Uri.parse('/api/products/${widget.productId}/price-history'),
        headers: {'Accept': 'application/json'},
      );

      final data = jsonDecode(response.body);

      if (response.statusCode == 200 && data['ok'] == true) {
        if (mounted) {
          setState(() {
            _priceHistory = data['data'] ?? [];
          });
        }
      } else {
        if (mounted) {
          setState(() {
            _priceHistoryError =
                data['error']?.toString() ?? 'Nie udało się pobrać historii zmian cen.';
          });
        }
      }
    } catch (e) {
      debugPrint("Błąd historii cen: $e");

      if (mounted) {
        setState(() {
          _priceHistoryError = e.toString();
        });
      }
    } finally {
      if (mounted) {
        setState(() => _isHistoryLoading = false);
      }
    }
  }

  List<ReportOffer> get _reportOffers => _competitors
      .map((comp) => ReportOffer(
            store: comp['shop_label']?.toString() ?? comp['store']?.toString() ?? 'Nieznany sklep',
            sku: comp['sku']?.toString(),
            name: comp['comp_name']?.toString() ?? comp['name']?.toString(),
            url: comp['url']?.toString(),
            price: _toNum(comp['display_price'] ??
                comp['comp_price_special'] ??
                comp['price_special'] ??
                comp['comp_price_normal'] ??
                comp['price_normal']),
          ))
      .toList();

  // Jeden panel zgłoszeń dla całego widoku; przyciski przy ofercie i rekomendacji od razu wybierają temat.
  void _openReport({ReportTopic? topic, int? offerIndex}) {
    showReportIssueSheet(
      context,
      productId: widget.productId,
      productName: _product?['name']?.toString() ?? 'Produkt',
      offers: _reportOffers,
      recommendation: _recommendedPrice != null
          ? ReportRecommendation(price: _recommendedPrice!, reason: _recommendationReason ?? '')
          : null,
      topic: topic,
      offerIndex: offerIndex,
    );
  }

  ButtonStyle _smallReportButtonStyle(ColorScheme colorScheme) => TextButton.styleFrom(
        foregroundColor: colorScheme.onSurfaceVariant,
        padding: const EdgeInsets.symmetric(horizontal: 6),
        minimumSize: const Size(0, 28),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        visualDensity: VisualDensity.compact,
      );

  // ---- NAPRAWIONA FUNKCJA Z OBSŁUGĄ BŁĘDÓW ----
  Future<void> _getRecommendation() async {
    if (mounted) setState(() => _isRecommending = true);

    try {
      final url = Uri.parse("/api/products/${widget.productId}/recommend");
      final response = await ApiClient.post(
        url,
        headers: {'Accept': 'application/json'},
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['ok'] == true && mounted) {
          final rawRecommendation = data['recommendation'];
          setState(() {
            _recommendedPrice = rawRecommendation is num
                ? rawRecommendation.toDouble()
                : double.tryParse(rawRecommendation?.toString() ?? '');
            _recommendationReason = data['reason']?.toString();
          });
        } else if (mounted) {
          // Jeśli Python zwróci błąd, wyświetlimy go na czerwonym pasku!
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Błąd AI: ${data['error'] ?? 'Nieznany błąd'}'),
              backgroundColor: Colors.red,
            ),
          );
        }
      } else if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Błąd serwera (HTTP ${response.statusCode})'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } catch (e) {
      debugPrint("Błąd AI: $e");
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Błąd połączenia: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _isRecommending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final hasMatches = _competitors.isNotEmpty;

    return Scaffold(
      backgroundColor: colorScheme.surface,
      appBar: AppBar(
        title: Text(
          "Szczegóły produktu",
          style: GoogleFonts.overpass(fontWeight: FontWeight.w600),
        ),
        backgroundColor: colorScheme.surface,
        elevation: 0,
        centerTitle: false,
        actions: [
          if (_product != null)
            TextButton.icon(
              key: const Key('report_issue_button'),
              onPressed: () => _openReport(),
              icon: const Icon(Icons.flag_outlined, size: 20),
              label: const Text('Zgłoś błąd'),
            ),
          const SizedBox(width: 8),
          IconButton(
            icon: const Icon(Icons.refresh, size: 28),
            tooltip: 'Odśwież',
            onPressed: _fetchDetails,
          ),
          const SizedBox(width: 16),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _product == null
              ? const Center(child: Text("Nie znaleziono produktu."))
              : Padding(
                  padding: const EdgeInsets.all(32.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _buildProductHeader(colorScheme),
                      const SizedBox(height: 24),

                      if (hasMatches) ...[
                        DefaultTabController(
                          length: 3,
                          child: Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                Container(
                                  decoration: BoxDecoration(
                                    border: Border(
                                      bottom: BorderSide(
                                        color: colorScheme.outlineVariant.withOpacity(0.5),
                                      ),
                                    ),
                                  ),
                                  child: TabBar(
                                    labelColor: colorScheme.primary,
                                    unselectedLabelColor: colorScheme.onSurfaceVariant,
                                    indicatorColor: colorScheme.primary,
                                    indicatorWeight: 3,
                                    tabs: const [
                                      Tab(text: "Porównanie cen"),
                                      Tab(text: "Specyfikacja"),
                                      Tab(text: "Historia zmian"),
                                    ],
                                  ),
                                ),
                                const SizedBox(height: 24),
                                Expanded(
                                  child: TabBarView(
                                    children: [
                                      SingleChildScrollView(
                                        child: Row(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Expanded(
                                              flex: 2,
                                              child: _buildCompetitorsSection(colorScheme),
                                            ),
                                            if (widget.showPriceRecommendation) ...[
                                              const SizedBox(width: 32),
                                              Expanded(
                                                flex: 1,
                                                child: _buildRecommendationSection(colorScheme),
                                              ),
                                            ],
                                          ],
                                        ),
                                      ),
                                      SingleChildScrollView(
                                        child: _buildSpecificationTab(colorScheme),
                                      ),
                                      SingleChildScrollView(
                                        child: _buildPriceHistoryTab(colorScheme),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ] else ...[
                        // Wyświetlamy panel AI obok specyfikacji nawet dla produktów bez konkurencji
                        Expanded(
                          child: SingleChildScrollView(
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Expanded(
                                  flex: 2,
                                  child: _buildSpecificationTab(colorScheme),
                                ),
                                if (widget.showPriceRecommendation) ...[
                                  const SizedBox(width: 32),
                                  Expanded(
                                    flex: 1,
                                    child: _buildRecommendationSection(colorScheme),
                                  ),
                                ],
                              ],
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
    );
  }

  Widget _buildProductHeader(ColorScheme colorScheme) {
    final String name = _product?['name']?.toString() ?? 'Brak nazwy';
    final String sku = _product?['sku']?.toString() ?? 'N/A';
    final num? displayPrice = _toNum(
      _product?['display_price'] ??
          _product?['price_special'] ??
          _product?['price_normal'] ??
          _product?['price'],
    );

    final String imageUrl = _product?['image']?.toString() ?? '';
    final String size = _product?['size']?.toString() ?? '';
    final String color = _product?['color']?.toString() ?? '';
    final String details = [
      "SKU: $sku",
      if (size.isNotEmpty) "Rozmiar: $size",
      if (color.isNotEmpty) "Kolor: $color",
    ].join(' | ');
    final String productUrl = clientProductUrl(_product?['url']?.toString());

    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: colorScheme.surfaceContainerHighest.withOpacity(0.3),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.3)),
      ),
      child: Row(
        children: [
          Container(
            width: 120,
            height: 120,
            decoration: BoxDecoration(
              color: colorScheme.surface,
              borderRadius: BorderRadius.circular(16),
            ),
            child: imageUrl.isNotEmpty
                ? ClipRRect(
                    borderRadius: BorderRadius.circular(16),
                    child: Image.network(
                      imageUrl,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) =>
                          const Icon(Icons.image_not_supported, size: 40),
                    ),
                  )
                : const Icon(Icons.inventory, size: 40),
          ),
          const SizedBox(width: 32),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  name,
                  style: GoogleFonts.overpass(
                    fontSize: 28,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  details,
                  style: TextStyle(
                    color: colorScheme.onSurfaceVariant,
                    fontSize: 16,
                  ),
                ),
                if (productUrl.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  InkWell(
                    key: const Key('client_product_link'),
                    onTap: () => _launchCompetitorUrl(productUrl),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.open_in_new, size: 16, color: colorScheme.primary),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            "Zobacz produkt w sklepie",
                            style: TextStyle(
                              color: colorScheme.primary,
                              fontSize: 14,
                              fontWeight: FontWeight.bold,
                              decoration: TextDecoration.underline,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                displayPrice != null
                    ? "${displayPrice.toStringAsFixed(2)} zł"
                    : "Brak ceny",
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 32,
                  color: colorScheme.primary,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildSpecificationTab(ColorScheme colorScheme) {
    final String category = _product?['category']?.toString() ?? '-';
    final String manufacturer = _product?['manufacturer']?.toString() ?? '-';
    final String color = _product?['color']?.toString() ?? '-';
    final String size = _product?['size']?.toString() ?? '-';
    final String availability = _product?['availability']?.toString() ?? '-';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          "Specyfikacja techniczna",
          style: GoogleFonts.overpass(fontSize: 20, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 16),
        Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.3)),
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Column(
              children: [
                _buildSimpleSpecRow("Kategoria", category, true, colorScheme),
                _buildSimpleSpecRow("Producent", manufacturer, false, colorScheme),
                _buildSimpleSpecRow("Kolor", color, true, colorScheme),
                _buildSimpleSpecRow("Rozmiar", size, false, colorScheme),
                _buildSimpleSpecRow("Dostępność", availability, true, colorScheme),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildPriceHistoryTab(ColorScheme colorScheme) {
    if (!_hasPriceHistoryAccess) {
      return _buildLockedPriceHistoryPanel(colorScheme);
    }

    if (_isHistoryLoading) {
      return const Padding(
        padding: EdgeInsets.all(48),
        child: Center(child: CircularProgressIndicator()),
      );
    }

    if (_priceHistoryError != null) {
      return Container(
        padding: const EdgeInsets.all(24),
        decoration: BoxDecoration(
          color: colorScheme.errorContainer.withOpacity(0.5),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Text(
          _priceHistoryError!,
          style: TextStyle(
            color: colorScheme.error,
            fontWeight: FontWeight.w600,
          ),
        ),
      );
    }

    if (_priceHistory.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(24),
        decoration: BoxDecoration(
          color: colorScheme.surfaceContainerHighest.withOpacity(0.3),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: colorScheme.outlineVariant.withOpacity(0.3)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              "Brak historii zmian cen",
              style: GoogleFonts.overpass(
                fontSize: 20,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              "Historia pojawi się dopiero wtedy, gdy scraper wykryje zmianę ceny u konkurencji.",
              style: TextStyle(
                color: colorScheme.onSurfaceVariant,
                fontSize: 14,
              ),
            ),
          ],
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          "Historia zmian cen konkurencji",
          style: GoogleFonts.overpass(
            fontSize: 20,
            fontWeight: FontWeight.bold,
          ),
        ),
        const SizedBox(height: 16),
        ListView.separated(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: _priceHistory.length,
          separatorBuilder: (context, index) => const SizedBox(height: 12),
          itemBuilder: (context, index) {
            final item = _priceHistory[index];

            final String store = item['store']?.toString() ?? 'Nieznany sklep';
            final String name = item['name']?.toString() ?? 'Nieznany produkt';
            final String sku = item['sku']?.toString() ?? '-';
            final String url = item['url']?.toString() ?? '';

            final num? oldPrice = _toNum(item['old_display_price']);
            final num? newPrice = _toNum(item['new_display_price']);
            final num? difference = _toNum(item['difference']);

            final String date = _formatHistoryDate(item['valid_from']);

            final bool wentDown = difference != null && difference < 0;
            final bool wentUp = difference != null && difference > 0;

            final Color diffColor = wentDown
                ? Colors.green
                : wentUp
                    ? colorScheme.error
                    : colorScheme.onSurfaceVariant;

            final String diffText = difference == null
                ? "-"
                : difference == 0
                    ? "bez zmian"
                    : difference > 0
                        ? "+${difference.toStringAsFixed(2)} zł"
                        : "${difference.toStringAsFixed(2)} zł";

            return Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: colorScheme.surface,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: colorScheme.outlineVariant.withOpacity(0.4),
                ),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: colorScheme.primaryContainer.withOpacity(0.6),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Icon(
                      Icons.history,
                      color: colorScheme.primary,
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          name,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontWeight: FontWeight.w800,
                            fontSize: 15,
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          "Sklep: $store • SKU: $sku",
                          style: TextStyle(
                            color: colorScheme.onSurfaceVariant,
                            fontSize: 13,
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          "Data zmiany: $date",
                          style: TextStyle(
                            color: colorScheme.onSurfaceVariant,
                            fontSize: 13,
                          ),
                        ),
                        if (url.isNotEmpty) ...[
                          const SizedBox(height: 8),
                          InkWell(
                            onTap: () => _launchCompetitorUrl(url),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(
                                  Icons.open_in_new,
                                  size: 14,
                                  color: colorScheme.primary,
                                ),
                                const SizedBox(width: 4),
                                Text(
                                  "Otwórz ofertę",
                                  style: TextStyle(
                                    color: colorScheme.primary,
                                    fontSize: 13,
                                    fontWeight: FontWeight.bold,
                                    decoration: TextDecoration.underline,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(width: 16),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        oldPrice != null ? "${oldPrice.toStringAsFixed(2)} zł" : "-",
                        style: TextStyle(
                          color: colorScheme.onSurfaceVariant,
                          fontSize: 13,
                          decoration: TextDecoration.lineThrough,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        newPrice != null ? "${newPrice.toStringAsFixed(2)} zł" : "-",
                        style: TextStyle(
                          color: colorScheme.primary,
                          fontWeight: FontWeight.bold,
                          fontSize: 18,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: diffColor.withOpacity(0.12),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(
                          diffText,
                          style: TextStyle(
                            color: diffColor,
                            fontWeight: FontWeight.bold,
                            fontSize: 12,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            );
          },
        ),
      ],
    );
  }

  Widget _buildLockedPriceHistoryPanel(ColorScheme colorScheme) {
    return Container(
      padding: const EdgeInsets.all(32),
      decoration: BoxDecoration(
        color: colorScheme.secondaryContainer.withOpacity(0.6),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: colorScheme.secondary.withOpacity(0.2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Icon(
            Icons.lock_outline,
            size: 56,
            color: colorScheme.secondary.withOpacity(0.7),
          ),
          const SizedBox(height: 16),
          Text(
            "Historia zmian cen",
            style: GoogleFonts.overpass(
              fontSize: 22,
              fontWeight: FontWeight.bold,
              color: colorScheme.onSecondaryContainer,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            "Ta funkcja jest dostępna w pakietach Pro i Premium.",
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 14,
              color: colorScheme.onSecondaryContainer.withOpacity(0.8),
            ),
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            key: const Key('change_plan_history'),
            onPressed: _openPlans,
            icon: const Icon(Icons.workspace_premium),
            label: const Text("Zmień pakiet"),
          ),
        ],
      ),
    );
  }

  String _formatHistoryDate(dynamic value) {
    if (value == null) return "-";

    final parsed = DateTime.tryParse(value.toString());

    if (parsed == null) {
      return value.toString();
    }

    final local = parsed.toLocal();

    String twoDigits(int number) => number.toString().padLeft(2, '0');

    final day = twoDigits(local.day);
    final month = twoDigits(local.month);
    final year = local.year.toString();
    final hour = twoDigits(local.hour);
    final minute = twoDigits(local.minute);

    return "$day.$month.$year $hour:$minute";
  }

  Widget _buildSimpleSpecRow(String label, String value, bool isEven, ColorScheme colorScheme) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      color: isEven ? colorScheme.surfaceContainerHighest.withOpacity(0.3) : colorScheme.surface,
      child: Row(
        children: [
          Expanded(
            flex: 2,
            child: Text(
              label,
              style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 14),
            ),
          ),
          Expanded(
            flex: 3,
            child: Text(
              value,
              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCompetitorsSection(ColorScheme colorScheme) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          "Zarejestrowane oferty rynku",
          style: GoogleFonts.overpass(fontSize: 20, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 16),
        _competitors.isEmpty
            ? Container(
                padding: const EdgeInsets.all(24),
                decoration: BoxDecoration(
                  color: colorScheme.surfaceContainerHighest.withOpacity(0.3),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: const Text("Brak danych o konkurencji."),
              )
            : ListView.separated(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: _competitors.length,
                separatorBuilder: (context, index) =>
                    Divider(height: 1, color: colorScheme.outlineVariant.withOpacity(0.3)),
                itemBuilder: (context, index) {
                  final comp = _competitors[index];
                  final String compName =
                      comp['comp_name']?.toString() ?? comp['name']?.toString() ?? 'Nieznany produkt';
                  final String shopName =
                      comp['shop_label']?.toString() ?? comp['store']?.toString() ?? 'Nieznany sklep';
                  final num? price = _toNum(
                    comp['display_price'] ??
                        comp['comp_price_special'] ??
                        comp['price_special'] ??
                        comp['comp_price_normal'] ??
                        comp['price_normal'],
                  );
                  final String url = comp['url']?.toString() ?? '';
                  final String imageUrl = comp['image']?.toString() ?? '';
                  final num? priceDifference = _toNum(comp['price_difference']);

                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Container(
                          width: 60,
                          height: 60,
                          decoration: BoxDecoration(
                            color: colorScheme.surfaceContainerHighest.withOpacity(0.5),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: imageUrl.isNotEmpty
                              ? ClipRRect(
                                  borderRadius: BorderRadius.circular(10),
                                  child: Image.network(
                                    imageUrl,
                                    fit: BoxFit.cover,
                                    errorBuilder: (context, error, stackTrace) =>
                                        const Icon(Icons.image_not_supported, size: 24),
                                  ),
                                )
                              : const Icon(Icons.image_not_supported, size: 24),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                compName,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                "Sklep: $shopName",
                                style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12),
                              ),
                              if (url.isNotEmpty) ...[
                                const SizedBox(height: 4),
                                InkWell(
                                  onTap: () => _launchCompetitorUrl(url),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(Icons.open_in_new, size: 12, color: colorScheme.primary),
                                      const SizedBox(width: 4),
                                      Flexible(
                                        child: Text(
                                          "Otwórz ofertę",
                                          style: TextStyle(
                                            color: colorScheme.primary,
                                            fontSize: 12,
                                            fontWeight: FontWeight.bold,
                                            decoration: TextDecoration.underline,
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                        const SizedBox(width: 16),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            Text(
                              price != null ? "${price.toStringAsFixed(2)} zł" : "Brak ceny",
                              style: TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 16,
                                color: colorScheme.primary,
                              ),
                            ),
                            if (priceDifference != null) ...[
                              const SizedBox(height: 6),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                                decoration: BoxDecoration(
                                  color: priceDifference == 0
                                      ? colorScheme.surfaceContainerHighest
                                      : priceDifference > 0
                                          ? Colors.green.withOpacity(0.15)
                                          : colorScheme.errorContainer,
                                  borderRadius: BorderRadius.circular(6),
                                ),
                                child: Text(
                                  priceDifference == 0
                                      ? "Cena równa"
                                      : priceDifference > 0
                                          ? "+ ${priceDifference.toStringAsFixed(2)} zł drożej"
                                          : "- ${priceDifference.abs().toStringAsFixed(2)} zł taniej",
                                  style: TextStyle(
                                    color: priceDifference == 0
                                        ? colorScheme.onSurface
                                        : priceDifference > 0
                                            ? Colors.green[800]
                                            : colorScheme.error,
                                    fontSize: 11,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ),
                            ],
                            const SizedBox(height: 4),
                            TextButton.icon(
                              key: Key('report_offer_button_$index'),
                              onPressed: () => _openReport(topic: ReportTopic.comparison, offerIndex: index),
                              style: _smallReportButtonStyle(colorScheme),
                              icon: const Icon(Icons.flag_outlined, size: 14),
                              label: const Text('Zgłoś', style: TextStyle(fontSize: 11)),
                            ),
                          ],
                        ),
                      ],
                    ),
                  );
                },
              ),
      ],
    );
  }

  Widget _buildRecommendationSection(ColorScheme colorScheme) {
    final hasPremium = _subscriptionPlan == 'Premium';
    final hasMatches = _competitors.isNotEmpty;

    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: colorScheme.secondaryContainer.withOpacity(0.6),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: colorScheme.secondary.withOpacity(0.2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(Icons.auto_awesome, color: colorScheme.secondary),
              const SizedBox(width: 10),
              Text(
                "Inteligentna wycena",
                style: GoogleFonts.overpass(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                  color: colorScheme.onSecondaryContainer,
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          if (!hasPremium) ...[
            Icon(Icons.lock_outline, size: 48, color: colorScheme.secondary.withOpacity(0.5)),
            const SizedBox(height: 16),
            Text(
              "Wymagany pakiet Premium",
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.bold,
                color: colorScheme.onSecondaryContainer,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              "Model AI przeanalizuje rynek i wskaże idealną cenę.",
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 13,
                color: colorScheme.onSecondaryContainer.withOpacity(0.8),
              ),
            ),
            const SizedBox(height: 16),
            Center(
              child: FilledButton.icon(
                key: const Key('change_plan_recommendation'),
                onPressed: _openPlans,
                icon: const Icon(Icons.workspace_premium),
                label: const Text("Zmień pakiet"),
              ),
            ),
          ] else ...[
            if (_recommendedPrice != null) ...[
              Text(
                "Sugerowana cena:",
                style: TextStyle(
                  color: colorScheme.onSecondaryContainer.withOpacity(0.8),
                  fontSize: 13,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                "${_recommendedPrice!.toStringAsFixed(2)} zł",
                style: TextStyle(
                  fontSize: 38,
                  fontWeight: FontWeight.w900,
                  color: colorScheme.secondary,
                ),
              ),
              const SizedBox(height: 16),
              Text(
                _recommendationReason ?? "",
                style: TextStyle(
                  fontSize: 13,
                  height: 1.5,
                  color: colorScheme.onSecondaryContainer,
                ),
              ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  key: const Key('report_recommendation_button'),
                  onPressed: () => _openReport(topic: ReportTopic.recommendation),
                  style: _smallReportButtonStyle(colorScheme),
                  icon: const Icon(Icons.flag_outlined, size: 14),
                  label: const Text('Zgłoś problem z rekomendacją', style: TextStyle(fontSize: 12)),
                ),
              ),
              const SizedBox(height: 16),
            ] else ...[
              Text(
                hasMatches
                    ? "Model AI jest gotowy do analizy tego produktu."
                    : "Model AI nie ma danych rynkowych dla tego produktu.",
                style: TextStyle(
                  fontSize: 13,
                  height: 1.5,
                  color: colorScheme.onSecondaryContainer.withOpacity(0.8),
                ),
              ),
              const SizedBox(height: 24),
            ],
            FilledButton.icon(
              onPressed: _isRecommending || !hasMatches ? null : _getRecommendation,
              style: FilledButton.styleFrom(
                backgroundColor: colorScheme.secondary,
                foregroundColor: colorScheme.onSecondary,
                padding: const EdgeInsets.symmetric(vertical: 18),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
              ),
              icon: _isRecommending
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
                    )
                  : (hasMatches ? const Icon(Icons.psychology) : const Icon(Icons.block)),
              label: Text(
                _isRecommending
                    ? "Analizowanie..."
                    : (hasMatches ? "Generuj cenę" : "Brak ofert z rynku"),
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ],
      ),
    );
  }

  num? _toNum(dynamic value) {
    if (value == null) return null;
    if (value is num) return value;
    return num.tryParse(value.toString());
  }
}