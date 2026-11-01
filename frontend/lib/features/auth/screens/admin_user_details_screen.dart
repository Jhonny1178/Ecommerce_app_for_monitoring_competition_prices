import 'package:flutter/material.dart';
import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/core/utils/dialog_utils.dart';
import 'dart:convert';

class AdminUserDetailsScreen extends StatefulWidget {
  final int userId;

  const AdminUserDetailsScreen({super.key, required this.userId});

  @override
  State<AdminUserDetailsScreen> createState() => _AdminUserDetailsScreenState();
}

class _AdminUserDetailsScreenState extends State<AdminUserDetailsScreen> {
  bool _isLoading = true;
  Map<String, dynamic>? _user;
  List<dynamic> _logs = [];

  List<dynamic> _parseList(dynamic value) {
    if (value is List) return value;
    if (value is String) {
      try {
        final parsed = jsonDecode(value);
        if (parsed is List) return parsed;
      } catch (_) {}
    }
    return [];
  }

  @override
  void initState() {
    super.initState();
    _fetchDetails();
  }

  Future<void> _fetchDetails() async {
    setState(() => _isLoading = true);
    try {
      final response = await ApiClient.get(Uri.parse("/api/admin/user_logs/${widget.userId}"));
      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['ok'] == true) {
        setState(() {
          _user = data['user'];
          _logs = data['logs'] ?? [];
        });
      }
    } catch (e) {
      debugPrint("Error fetching user details: $e");
    } finally {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _approveUser() async {
    try {
      final response = await ApiClient.post(
        Uri.parse("/api/admin/approve_user"),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'user_id': widget.userId}),
      );
      if (response.statusCode == 200) {
        if (mounted) Navigator.of(context).pop();
      }
    } catch (e) {
      debugPrint("Error approving user: $e");
    }
  }

  Future<void> _rejectUser() async {
    final confirmed = await DialogUtils.confirmRejection(context, _user?['email']?.toString());
    if (!confirmed) return;

    try {
      final response = await ApiClient.post(
        Uri.parse("/api/admin/reject_user"),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'user_id': widget.userId}),
      );
      final data = jsonDecode(response.body);

      if (!mounted) return;

      if (response.statusCode == 200) {
        if (data['warning'] != null) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(data['warning'].toString()), backgroundColor: Colors.orange),
          );
        }
        Navigator.of(context).pop();
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(data['error']?.toString() ?? 'Nie udało się odrzucić wniosku.'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } catch (e) {
      debugPrint("Error rejecting user: $e");
    }
  }

  Widget _buildTimeline(List<dynamic> urlLogs, ColorScheme colorScheme) {
    return ListView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      itemCount: urlLogs.length,
      itemBuilder: (context, index) {
        final log = urlLogs[index];
        final status = log['status'];
        
        Color iconColor = Colors.grey;
        IconData icon = Icons.info_outline;
        
        if (status == 'success') {
          iconColor = Colors.green;
          icon = Icons.check_circle;
        } else if (status == 'error') {
          iconColor = Colors.red;
          icon = Icons.error;
        } else if (status == 'info') {
          iconColor = Colors.blue;
          icon = Icons.pending;
        }

        // Format datetime slightly
        final dtStr = log['created_at'];
        final dt = dtStr != null ? DateTime.tryParse(dtStr.toString()) ?? DateTime.now() : DateTime.now();
        final timeStr = "${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}:${dt.second.toString().padLeft(2, '0')}";

        return Padding(
          padding: const EdgeInsets.only(bottom: 16.0),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Column(
                children: [
                  Icon(icon, color: iconColor),
                  if (index != urlLogs.length - 1)
                    Container(
                      width: 2,
                      height: 40,
                      color: colorScheme.surfaceContainerHighest,
                      margin: const EdgeInsets.symmetric(vertical: 4),
                    )
                ],
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(log['step']?.toString() ?? 'Nieznany krok', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                        Text(timeStr, style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12)),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(log['message'] ?? '', style: TextStyle(color: colorScheme.onSurfaceVariant)),
                  ],
                ),
              )
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    if (_isLoading) {
      return Scaffold(
        appBar: AppBar(title: const Text('Szczegóły wniosku')),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    if (_user == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Szczegóły wniosku')),
        body: const Center(child: Text('Nie znaleziono użytkownika')),
      );
    }

    // Grupowanie logów po url
    final Map<String, List<dynamic>> groupedLogs = {};
    for (var log in _logs) {
      final url = log['url']?.toString() ?? 'Proces Główny';
      if (!groupedLogs.containsKey(url)) {
        groupedLogs[url] = [];
      }
      groupedLogs[url]!.add(log);
    }

    return Scaffold(
      appBar: AppBar(
        title: Text('${_user!['first_name']} ${_user!['last_name']} - Szczegóły'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _fetchDetails),
        ],
      ),
      body: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Lewy panel - informacje o użytkowniku i akcje
          Container(
            width: 350,
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              border: Border(right: BorderSide(color: colorScheme.surfaceContainerHighest)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Informacje o firmie', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                const SizedBox(height: 16),
                Text('Domena:', style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12)),
                Text(_user!['company_domain'] ?? '-', style: const TextStyle(fontSize: 16)),
                const SizedBox(height: 16),
                Text('Wskazana konkurencja:', style: TextStyle(color: colorScheme.onSurfaceVariant, fontSize: 12)),
                const SizedBox(height: 8),
                if (_user!['competitor_urls'] != null)
                  ...(_parseList(_user!['competitor_urls'])).map((url) => Padding(
                    padding: const EdgeInsets.only(bottom: 8.0),
                    child: Text('• $url', style: const TextStyle(fontSize: 14)),
                  )),
                const Spacer(),
                const Divider(),
                const SizedBox(height: 16),
                const Text('Decyzja', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    onPressed: _approveUser,
                    style: FilledButton.styleFrom(backgroundColor: Colors.green, padding: const EdgeInsets.all(16)),
                    child: const Text('Zatwierdź wniosek'),
                  ),
                ),
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton(
                    onPressed: _rejectUser,
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.red, padding: const EdgeInsets.all(16)),
                    child: const Text('Odrzuć wniosek'),
                  ),
                ),
              ],
            ),
          ),
          
          // Prawy panel - logi scrapera
          Expanded(
            child: _logs.isEmpty 
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.hourglass_empty, size: 64, color: colorScheme.surfaceContainerHighest),
                      const SizedBox(height: 16),
                      Text('Proces analizy jeszcze się nie rozpoczął', style: TextStyle(color: colorScheme.onSurfaceVariant)),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(32),
                  children: [
                    const Text('Postęp Generowania AI Scraperów', style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 24),
                    ...groupedLogs.entries.map((entry) {
                      final url = entry.key;
                      final urlLogs = entry.value;
                      
                      // Sprawdzenie czy dany proces ma błąd
                      final hasError = urlLogs.any((l) => l['status'] == 'error');
                      final isSuccess = urlLogs.any((l) => l['step'] == 'Spider Builder' && l['status'] == 'success');

                      Color headerColor = colorScheme.primaryContainer;
                      if (hasError) headerColor = Colors.red.withOpacity(0.1);
                      if (isSuccess) headerColor = Colors.green.withOpacity(0.1);

                      return Card(
                        margin: const EdgeInsets.only(bottom: 24),
                        elevation: 0,
                        shape: RoundedRectangleBorder(
                          side: BorderSide(color: colorScheme.surfaceContainerHighest),
                          borderRadius: BorderRadius.circular(16),
                        ),
                        child: Theme(
                          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
                          child: ExpansionTile(
                            initiallyExpanded: true,
                            backgroundColor: Colors.transparent,
                            collapsedBackgroundColor: headerColor,
                            title: Text(url, style: const TextStyle(fontWeight: FontWeight.bold)),
                            subtitle: Text(
                              hasError ? 'Wystąpił błąd podczas generowania' : (isSuccess ? 'Pająk wygenerowany pomyślnie' : 'W trakcie analizy...'),
                              style: TextStyle(color: hasError ? Colors.red : (isSuccess ? Colors.green : colorScheme.onSurfaceVariant)),
                            ),
                            children: [
                              Padding(
                                padding: const EdgeInsets.all(24.0),
                                child: _buildTimeline(urlLogs, colorScheme),
                              )
                            ],
                          ),
                        ),
                      );
                    }),
                  ],
                ),
          ),
        ],
      ),
    );
  }
}
