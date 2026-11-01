import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:tonten/core/api/api_client.dart';
import 'package:tonten/core/utils/validators.dart';

import 'auth_router.dart';
import 'login_screen.dart';

class FileUploadScreen extends StatefulWidget {
  const FileUploadScreen({super.key});

  @override
  State<FileUploadScreen> createState() => _FileUploadScreenState();
}

class _FileUploadScreenState extends State<FileUploadScreen> {
  final _formKey = GlobalKey<FormState>();

  final _storeNameController = TextEditingController();
  final _companyDomainController = TextEditingController();
  final _websiteUrlController = TextEditingController();
  final _sourceUrlController = TextEditingController();

  final List<_MappingRow> _mappingRows = [
    _MappingRow('NAZWA', 'name'),
    _MappingRow('CENA', 'price_normal'),
    _MappingRow('URL', 'url'),
    _MappingRow('SKU', 'sku'),
    _MappingRow('OPIS', 'description'),
    _MappingRow('KOLOR', 'color'),
    _MappingRow('MARKA', 'manufacturer'),
    _MappingRow('ROZMIAR', 'size'),
    _MappingRow('KATEGORIA', 'category'),
    _MappingRow('DOSTEPNOSC', 'availability'),
    _MappingRow('ZDJECIE', 'image'),
    _MappingRow('CENA_PROMO', 'price_special'),
  ];

  String _sourceType = 'local';
  String _fileFormat = 'csv';
  PlatformFile? _pickedFile;

  bool _isLoadingProfile = true;
  bool _isSaving = false;

  final _messageController = TextEditingController();
  bool _isSendingMessage = false;

  @override
  void initState() {
    super.initState();
    _loadProfile();
  }

  @override
  void dispose() {
    _storeNameController.dispose();
    _companyDomainController.dispose();
    _websiteUrlController.dispose();
    _sourceUrlController.dispose();
    _messageController.dispose();

    for (final row in _mappingRows) {
      row.externalController.dispose();
      row.internalController.dispose();
    }

    super.dispose();
  }

  Future<void> _loadProfile() async {
    try {
      final response = await ApiClient.get(
        Uri.parse('/api/me'),
        headers: {'Accept': 'application/json'},
      );

      if (response.statusCode != 200) {
        setState(() => _isLoadingProfile = false);
        return;
      }

      final data = jsonDecode(response.body);

      if (data['ok'] != true) {
        setState(() => _isLoadingProfile = false);
        return;
      }

      final user = data['user'] ?? {};
      final request = data['onboarding_request'] ?? {};
      final source = data['onboarding_source'] ?? {};
      final mappings = data['field_mappings'] as List? ?? [];

      _storeNameController.text =
          request['requested_store_name']?.toString() ??
          user['client_name']?.toString() ??
          '';

      _companyDomainController.text =
          request['company_domain']?.toString() ??
          user['company_domain']?.toString() ??
          '';

      _websiteUrlController.text =
          request['website_url']?.toString() ??
          user['client_website_url']?.toString() ??
          '';

      final loadedSourceType =
          request['source_type']?.toString() ??
          source['source_kind']?.toString();

      if (loadedSourceType == 'local' || loadedSourceType == 'url') {
        _sourceType = loadedSourceType!;
      }

      final loadedFileFormat =
          request['file_format']?.toString() ??
          source['file_format']?.toString();

      if (loadedFileFormat == 'excel') {
          _fileFormat = 'xlsx';
        } else if (['csv', 'xml', 'xlsx'].contains(loadedFileFormat)) {
          _fileFormat = loadedFileFormat!;
        }

      _sourceUrlController.text =
          request['source_url']?.toString() ??
          source['source_url']?.toString() ??
          '';

      if (mappings.isNotEmpty) {
        for (final row in _mappingRows) {
          row.externalController.dispose();
          row.internalController.dispose();
        }

        _mappingRows
          ..clear()
          ..addAll(
            mappings.map((m) {
              return _MappingRow(
                m['external_field']?.toString() ?? '',
                m['internal_field']?.toString() ?? '',
              );
            }),
          );
      }

      setState(() => _isLoadingProfile = false);
    } catch (e) {
      debugPrint('Błąd pobierania profilu: $e');
      setState(() => _isLoadingProfile = false);
    }
  }

  Future<void> _pickFile() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['csv', 'xml', 'xlsx', 'xls'],
      withData: true,
    );

    if (result == null || result.files.isEmpty) {
      return;
    }

    setState(() {
      _pickedFile = result.files.first;
    });
  }

  Map<String, String> _buildFieldMapping() {
    final mapping = <String, String>{};

    for (final row in _mappingRows) {
      final external = row.externalController.text.trim();
      final internal = row.internalController.text.trim();

      if (external.isNotEmpty && internal.isNotEmpty) {
        mapping[external] = internal;
      }
    }

    return mapping;
  }

  String? _validateOptionalWebsite(String? value) {
    final text = value?.trim() ?? '';
    if (text.isNotEmpty && !Validators.isWebsiteUrl(text)) {
      return Validators.invalidWebsite;
    }
    return null;
  }

  Future<bool> _confirmSubmission(Map<String, String> fieldMapping) async {
    final source = _sourceType == 'local'
        ? _pickedFile?.name ?? ''
        : _sourceUrlController.text.trim();

    final confirmed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Czy na pewno chcesz wysłać formularz?'),
        content: SizedBox(
          width: 500,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Sprawdź poniższe dane. Po wysłaniu formularza części z nich nie będzie można już zmienić.',
                ),
                const SizedBox(height: 16),
                Text('Sklep: ${_storeNameController.text.trim()}'),
                Text('Domena: ${_companyDomainController.text.trim()}'),
                Text('Źródło produktów: $source'),
                Text('Format danych: ${_fileFormat.toUpperCase()}'),
                Text('Zmapowane pola: ${fieldMapping.length}'),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Wróć do edycji'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Tak, wyślij formularz'),
          ),
        ],
      ),
    );

    return confirmed == true;
  }

  Future<void> _submitOnboarding() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }

    final fieldMapping = _buildFieldMapping();

    if (fieldMapping.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Dodaj przynajmniej jedno mapowanie pól.'),
          backgroundColor: Colors.red,
        ),
      );
      return;
    }

    if (_sourceType == 'local' && _pickedFile == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Wybierz plik z produktami.'),
          backgroundColor: Colors.red,
        ),
      );
      return;
    }

    if (!await _confirmSubmission(fieldMapping) || !mounted) {
      return;
    }

    setState(() => _isSaving = true);

    try {
      final request = http.MultipartRequest(
        'POST',
        Uri.parse('/api/onboarding/submit'),
      );

      request.fields['source_type'] = _sourceType;
      request.fields['file_format'] = _fileFormat;
      request.fields['store_name'] = _storeNameController.text.trim();
      request.fields['company_domain'] = _companyDomainController.text.trim();
      request.fields['website_url'] = _websiteUrlController.text.trim();
      request.fields['field_mapping'] = jsonEncode(fieldMapping);

      if (_sourceType == 'url') {
        request.fields['source_url'] = _sourceUrlController.text.trim();
      }

      if (_sourceType == 'local' && _pickedFile != null) {
        if (_pickedFile!.bytes == null) {
          throw Exception('Nie udało się odczytać pliku.');
        }

        request.files.add(
          http.MultipartFile.fromBytes(
            'file',
            _pickedFile!.bytes!,
            filename: _pickedFile!.name,
          ),
        );
      }

      final streamedResponse = await request.send();
      final responseBody = await streamedResponse.stream.bytesToString();
      final data = jsonDecode(responseBody);

      if (!mounted) return;

      if (streamedResponse.statusCode == 200 && data['ok'] == true) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Konfiguracja zapisana poprawnie.'),
            backgroundColor: Colors.green,
          ),
        );

        Navigator.of(context).pushReplacement(
          MaterialPageRoute(
            builder: (_) => const AuthRouter(
              status: 'awaiting_payment',
              isAdmin: false,
            ),
          ),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(data['error']?.toString() ?? 'Błąd zapisu danych.'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Błąd zapisu: $e'),
          backgroundColor: Colors.red,
        ),
      );
    } finally {
      if (mounted) {
        setState(() => _isSaving = false);
      }
    }
  }

  Future<void> _sendMessage() async {
    if (_messageController.text.trim().isEmpty) return;

    setState(() => _isSendingMessage = true);

    try {
      final response = await ApiClient.post(
        Uri.parse('/api/send_message'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'message': _messageController.text.trim()}),
      );

      final data = jsonDecode(response.body);

      if (response.statusCode == 200 && data['ok'] == true) {
        if (mounted) {
          _messageController.clear();
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Wiadomość wysłana pomyślnie.'),
              backgroundColor: Colors.green,
            ),
          );
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(data['error'] ?? 'Wystąpił błąd'),
              backgroundColor: Colors.red,
            ),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Błąd połączenia: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _isSendingMessage = false);
    }
  }

  void _logout() async {
    await ApiClient.post(Uri.parse('/api/logout'));

    if (mounted) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()),
      );
    }
  }

  void _addMappingRow() {
    setState(() {
      _mappingRows.add(_MappingRow('', ''));
    });
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    if (_isLoadingProfile) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }

    return Scaffold(
      backgroundColor: colorScheme.surface,
      appBar: AppBar(
        title: const Text('Konfiguracja profilu klienta'),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: _logout,
          ),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 920),
          child: Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.all(32),
              children: [
                Text(
                  'Uzupełnij dane potrzebne do importu produktów',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 8),
                Text(
                  'Analizujemy podane przez Ciebie sklepy konkurencji. W czasie oczekiwania na akceptację wniosku możesz wypełnić ten formularz: wskaż, skąd pobierać Twoje produkty i jak dopasować pola z pliku lub feedu. Dzięki temu sprawdzimy, czy możemy połączyć dane z Twoim sklepem.',
                  style: TextStyle(color: colorScheme.onSurfaceVariant),
                ),
                const SizedBox(height: 32),

                _sectionTitle(context, '1. Dane sklepu'),

                TextFormField(
                  controller: _storeNameController,
                  decoration: const InputDecoration(
                    labelText: 'Nazwa sklepu / firmy',
                    border: OutlineInputBorder(),
                  ),
                  validator: (value) {
                    if (value == null || value.trim().isEmpty) {
                      return 'Podaj nazwę sklepu.';
                    }
                    return null;
                  },
                ),

                const SizedBox(height: 16),

                TextFormField(
                  controller: _companyDomainController,
                  decoration: const InputDecoration(
                    labelText: 'Domena sklepu, np. mojsklep.pl',
                    border: OutlineInputBorder(),
                  ),
                ),

                const SizedBox(height: 16),

                TextFormField(
                  controller: _websiteUrlController,
                  decoration: const InputDecoration(
                    labelText: 'Link do sklepu, np. https://mojsklep.pl',
                    border: OutlineInputBorder(),
                  ),
                  validator: _validateOptionalWebsite,
                ),

                const SizedBox(height: 32),

                _sectionTitle(context, '2. Źródło produktów'),

                DropdownButtonFormField<String>(
                  value: _sourceType,
                  decoration: const InputDecoration(
                    labelText: 'Typ źródła',
                    border: OutlineInputBorder(),
                  ),
                  items: const [
                    DropdownMenuItem(
                      value: 'local',
                      child: Text('Wgrywam plik CSV/XML/XLSX'),
                    ),
                    DropdownMenuItem(
                      value: 'url',
                      child: Text('Podaję URL do feedu/pliku'),
                    ),
                  ],
                  onChanged: (value) {
                    setState(() {
                      _sourceType = value ?? 'local';
                    });
                  },
                ),

                const SizedBox(height: 16),

                DropdownButtonFormField<String>(
                  value: _fileFormat,
                  decoration: const InputDecoration(
                    labelText: 'Format danych',
                    border: OutlineInputBorder(),
                  ),
                  items: const [
                    DropdownMenuItem(value: 'csv', child: Text('CSV')),
                    DropdownMenuItem(value: 'xml', child: Text('XML')),
                    DropdownMenuItem(value: 'xlsx', child: Text('XLSX')),
                  ],
                  onChanged: (value) {
                    setState(() {
                      _fileFormat = value ?? 'csv';
                    });
                  },
                ),

                const SizedBox(height: 16),

                if (_sourceType == 'local') ...[
                  OutlinedButton.icon(
                    onPressed: _pickFile,
                    icon: const Icon(Icons.upload_file),
                    label: Text(
                      _pickedFile == null
                          ? 'Wybierz plik'
                          : 'Wybrano: ${_pickedFile!.name}',
                    ),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.all(20),
                    ),
                  ),
                ],

                if (_sourceType == 'url') ...[
                  TextFormField(
                    controller: _sourceUrlController,
                    decoration: const InputDecoration(
                      labelText: 'URL do pliku/feedu',
                      hintText: 'https://mojsklep.pl/feed.xml',
                      border: OutlineInputBorder(),
                    ),
                    validator: (value) {
                      if (_sourceType == 'url' &&
                          (value == null || value.trim().isEmpty)) {
                        return 'Podaj URL źródła.';
                      }
                      return _validateOptionalWebsite(value);
                    },
                  ),
                ],

                const SizedBox(height: 32),

                _sectionTitle(context, '3. Mapowanie pól'),

                Text(
                  'Po lewej wpisz nazwę kolumny/pola z Twojego pliku, po prawej pole systemowe.',
                  style: TextStyle(color: colorScheme.onSurfaceVariant),
                ),

                const SizedBox(height: 16),

                ..._mappingRows.asMap().entries.map((entry) {
                  final index = entry.key;
                  final row = entry.value;

                  return Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Row(
                      children: [
                        Expanded(
                          child: TextFormField(
                            controller: row.externalController,
                            decoration: const InputDecoration(
                              labelText: 'Pole z pliku/feedu',
                              border: OutlineInputBorder(),
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        const Icon(Icons.arrow_forward),
                        const SizedBox(width: 12),
                        Expanded(
                          child: TextFormField(
                            controller: row.internalController,
                            decoration: const InputDecoration(
                              labelText: 'Pole systemowe',
                              border: OutlineInputBorder(),
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        IconButton(
                          tooltip: 'Usuń mapowanie',
                          icon: const Icon(Icons.delete_outline),
                          onPressed: _mappingRows.length <= 1
                              ? null
                              : () {
                                  setState(() {
                                    row.externalController.dispose();
                                    row.internalController.dispose();
                                    _mappingRows.removeAt(index);
                                  });
                                },
                        ),
                      ],
                    ),
                  );
                }),

                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: _addMappingRow,
                    icon: const Icon(Icons.add),
                    label: const Text('Dodaj mapowanie'),
                  ),
                ),

                const SizedBox(height: 32),

                FilledButton.icon(
                  onPressed: _isSaving ? null : _submitOnboarding,
                  icon: _isSaving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save),
                  label: Text(
                    _isSaving ? 'Zapisywanie...' : 'Zapisz konfigurację',
                  ),
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.all(20),
                  ),
                ),

                const SizedBox(height: 32),
                const Divider(),
                const SizedBox(height: 24),

                _sectionTitle(context, 'Masz pytania do wyceny / wdrożenia?'),

                TextField(
                  controller: _messageController,
                  maxLines: 4,
                  decoration: InputDecoration(
                    filled: true,
                    fillColor: colorScheme.surface,
                    hintText: 'Napisz swoją wiadomość tutaj...',
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                ),

                const SizedBox(height: 16),

                Align(
                  alignment: Alignment.centerRight,
                  child: FilledButton.icon(
                    onPressed: _isSendingMessage ? null : _sendMessage,
                    icon: _isSendingMessage
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(
                              color: Colors.white,
                              strokeWidth: 2,
                            ),
                          )
                        : const Icon(Icons.send),
                    label: const Text('Wyślij wiadomość'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _sectionTitle(BuildContext context, String title) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Text(
        title,
        style: Theme.of(context).textTheme.titleLarge?.copyWith(
              fontWeight: FontWeight.bold,
            ),
      ),
    );
  }
}

class _MappingRow {
  final TextEditingController externalController;
  final TextEditingController internalController;

  _MappingRow(String external, String internal)
      : externalController = TextEditingController(text: external),
        internalController = TextEditingController(text: internal);
}