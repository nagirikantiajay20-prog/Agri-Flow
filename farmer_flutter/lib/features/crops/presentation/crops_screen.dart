import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../../core/localization/app_localizations.dart';
import '../../../core/services/connectivity_service.dart';
import '../../../data/repositories/farmer_repository.dart';

class CropVariety {
  final String value;
  final String label;
  final String emoji;

  const CropVariety({required this.value, required this.label, required this.emoji});
}

const List<CropVariety> kCropVarieties = [
  CropVariety(value: 'Cotton', label: 'Cotton (Bollgard II)', emoji: '🌸'),
  CropVariety(value: 'Rice', label: 'Rice / Paddy (BPT 5204)', emoji: '🌾'),
  CropVariety(value: 'Maize', label: 'Hybrid Maize (NK 6240)', emoji: '🌽'),
  CropVariety(value: 'Groundnut', label: 'Groundnut (K-6)', emoji: '🥜'),
  CropVariety(value: 'Wheat', label: 'Wheat', emoji: '🌾'),
  CropVariety(value: 'Pulses', label: 'Red Gram / Pulses', emoji: '🌱'),
  CropVariety(value: 'Sugarcane', label: 'Sugarcane', emoji: '🎋'),
  CropVariety(value: 'Chilli', label: 'Chilli (Teja Variety)', emoji: '🌶️'),
  CropVariety(value: 'Turmeric', label: 'Turmeric', emoji: '🌿'),
  CropVariety(value: 'Other', label: 'Other Crop Variety', emoji: '🌱'),
];

String getCropEmoji(String? type) {
  if (type == null || type.isEmpty) return '🌱';
  final lower = type.toLowerCase();
  for (final c in kCropVarieties) {
    if (c.value.toLowerCase() == lower || c.label.toLowerCase().contains(lower)) {
      return c.emoji;
    }
  }
  return '🌱';
}

int getStageIndex(String? status) {
  if (status == null) return 0;
  final s = status.toLowerCase();
  if (s.contains('sow')) return 0;
  if (s.contains('grow') || s.contains('veg')) return 1;
  if (s.contains('matur') || s.contains('flower')) return 2;
  if (s.contains('harvest')) return 3;
  return 0;
}

const List<String> kGrowthStages = ['Sowing', 'Growing', 'Maturity', 'Harvest'];

class CropsScreen extends StatefulWidget {
  final String? initialCropId;
  const CropsScreen({super.key, this.initialCropId});

  @override
  State<CropsScreen> createState() => _CropsScreenState();
}

class _CropsScreenState extends State<CropsScreen> {
  final _repository = FarmerRepository();

  List<Map<String, dynamic>> _crops = [];
  List<Map<String, dynamic>> _visits = [];
  bool _isLoading = true;
  bool _isRetrying = false;
  String? _errorMessage;
  String? _farmerId;

  String _selectedFilter = 'All';
  String? _activeInitialCropId;

  String _formatScheduledDate(String? rawDate) {
    if (rawDate == null || rawDate.trim().isEmpty) return 'Not scheduled yet';
    try {
      final dt = DateTime.parse(rawDate);
      final months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      return '${dt.day} ${months[dt.month - 1]} ${dt.year}';
    } catch (_) {
      return rawDate;
    }
  }

  @override
  void initState() {
    super.initState();
    _activeInitialCropId = widget.initialCropId;
    ConnectivityService().addOnRestoredListener(_onConnectivityRestored);
    _loadData();
  }

  @override
  void dispose() {
    ConnectivityService().removeOnRestoredListener(_onConnectivityRestored);
    super.dispose();
  }

  void _onConnectivityRestored() {
    // Clear stale connectivity error banner when internet returns
    if (mounted && _errorMessage != null) {
      setState(() {
        _errorMessage = null;
      });
    }
  }

  Future<void> _loadData() async {
    if (_isRetrying) return;
    if (_crops.isEmpty) {
      setState(() {
        _isLoading = true;
        _errorMessage = null;
      });
    } else {
      setState(() {
        _isRetrying = true;
      });
    }

    try {
      final fIdFuture = _repository.getFarmerId();
      final cropsFuture = _repository.getCrops();
      final visitsFuture = _repository.getVisits().catchError((_) => <Map<String, dynamic>>[]);

      final results = await Future.wait([fIdFuture, cropsFuture, visitsFuture]);
      final fId = results[0];
      final cropsData = results[1] as List<Map<String, dynamic>>;
      final visitsData = results[2] as List<Map<String, dynamic>>;

      _farmerId = fId?.toString();

      if (mounted) {
        setState(() {
          _crops = cropsData;
          _visits = visitsData;
          _isLoading = false;
          _isRetrying = false;
          _errorMessage = null; // Stale error cleared on success!
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _errorMessage = context.tr('errorLoad');
          _isLoading = false;
          _isRetrying = false;
        });
      }
    }
  }

  double get _totalAcres {
    return _crops.fold<double>(
      0.0,
      (acc, c) => acc + ((c['acres'] as num?)?.toDouble() ?? 0.0),
    );
  }

  List<Map<String, dynamic>> get _filteredCrops {
    if (_activeInitialCropId != null && _activeInitialCropId!.isNotEmpty && _selectedFilter == 'All') {
      final matched = _crops.where((c) => c['id']?.toString() == _activeInitialCropId).toList();
      if (matched.isNotEmpty) return matched;
    }
    if (_selectedFilter == 'All') return _crops;
    return _crops.where((c) {
      final type = (c['crop_type'] as String? ?? '').toLowerCase();
      return type.contains(_selectedFilter.toLowerCase());
    }).toList();
  }

  void _showAddCropModal() {
    String selectedType = 'Cotton';
    final customTypeController = TextEditingController();
    final acresController = TextEditingController(text: '2.5');
    final sowingDateController = TextEditingController(
      text: DateTime.now().toIso8601String().split('T')[0],
    );
    final locationController = TextEditingController(text: 'North Canal Plot');
    bool submitting = false;
    String? modalError;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(26)),
      ),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (modalContext, setModalState) {
            return Padding(
              padding: EdgeInsets.only(
                left: 20,
                right: 20,
                top: 20,
                bottom: MediaQuery.of(modalContext).viewInsets.bottom + 24,
              ),
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Modal Header
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Row(
                          children: [
                            Container(
                              width: 38,
                              height: 38,
                              decoration: BoxDecoration(
                                color: const Color(0xFFE7F4EB),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: const Center(
                                child: Text('🌱', style: TextStyle(fontSize: 20)),
                              ),
                            ),
                            const SizedBox(width: 10),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  context.tr('addCropBtn'),
                                  style: const TextStyle(
                                    fontFamily: 'Fraunces',
                                    fontSize: 20,
                                    fontWeight: FontWeight.w800,
                                    color: Color(0xFF15302A),
                                  ),
                                ),
                                Text(
                                  context.tr('manageCropRegistrations'),
                                  style: const TextStyle(
                                    fontSize: 12,
                                    color: Color(0xFF64748B),
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                        IconButton(
                          icon: const Icon(Icons.close, color: Color(0xFF64748B)),
                          onPressed: () => Navigator.pop(modalContext),
                        ),
                      ],
                    ),
                    const SizedBox(height: 18),

                    // Crop Type Dropdown
                    Text(
                      '${context.tr('cropType')} *',
                      style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    DropdownButtonFormField<String>(
                      value: selectedType,
                      decoration: InputDecoration(
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                      ),
                      items: kCropVarieties.map((c) {
                        return DropdownMenuItem(
                          value: c.value,
                          child: Text('${c.emoji}  ${c.label}', style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                        );
                      }).toList(),
                      onChanged: (val) {
                        if (val != null) {
                          setModalState(() => selectedType = val);
                        }
                      },
                    ),

                    // Custom variety input if "Other"
                    if (selectedType == 'Other') ...[
                      const SizedBox(height: 12),
                      Text(
                        '${context.tr('cropType')} (Custom) *',
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                      ),
                      const SizedBox(height: 6),
                      TextField(
                        controller: customTypeController,
                        decoration: InputDecoration(
                          hintText: 'e.g. Sunflower / Mustard',
                          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                        ),
                      ),
                    ],

                    const SizedBox(height: 14),
                    // Acres Field
                    Text(
                      '${context.tr('acres')} *',
                      style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: acresController,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      maxLength: 7,
                      decoration: InputDecoration(
                        counterText: '',
                        hintText: 'Number of acres (e.g. 4.5)',
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                      ),
                    ),

                    const SizedBox(height: 14),
                    // Sowing Date Field
                    Text(
                      '${context.tr('sowingDate')} *',
                      style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: sowingDateController,
                      readOnly: true,
                      decoration: InputDecoration(
                        suffixIcon: const Icon(Icons.calendar_today, size: 18, color: Color(0xFF64748B)),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                      ),
                      onTap: () async {
                        final picked = await showDatePicker(
                          context: modalContext,
                          initialDate: DateTime.now(),
                          firstDate: DateTime(2020),
                          lastDate: DateTime(2030),
                        );
                        if (picked != null) {
                          setModalState(() {
                            sowingDateController.text = picked.toIso8601String().split('T')[0];
                          });
                        }
                      },
                    ),

                    const SizedBox(height: 14),
                    // Plot Location Note
                    Text(
                      '${context.tr('plotLocation')} (Optional)',
                      style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: locationController,
                      decoration: InputDecoration(
                        hintText: 'e.g. North Canal Plot',
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5)),
                      ),
                    ),

                    const SizedBox(height: 14),
                    // Auto-schedule Notice Banner
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFFF0FDF4),
                        borderRadius: BorderRadius.circular(14),
                        border: Border.all(color: const Color(0xFFBBF7D0)),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('📅', style: TextStyle(fontSize: 14)),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              context.tr('autoScheduledNotice'),
                              style: const TextStyle(
                                fontSize: 12,
                                color: Color(0xFF166534),
                                fontWeight: FontWeight.w600,
                                height: 1.4,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                    if (modalError != null) ...[
                      const SizedBox(height: 14),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        decoration: BoxDecoration(
                          color: const Color(0xFFFEE2E2),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: const Color(0xFFFCA5A5)),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.error_outline, color: Color(0xFF991B1B), size: 18),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                modalError!,
                                style: const TextStyle(color: Color(0xFF991B1B), fontSize: 12.5, fontWeight: FontWeight.w700),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],

                    const SizedBox(height: 20),
                    // Actions
                    Row(
                      children: [
                        Expanded(
                          flex: 1,
                          child: SizedBox(
                            height: 48,
                            child: TextButton(
                              onPressed: () => Navigator.pop(modalContext),
                              style: TextButton.styleFrom(
                                backgroundColor: const Color(0xFFF1F5F9),
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                              ),
                              child: Text(
                                context.tr('cancel'),
                                style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          flex: 2,
                          child: SizedBox(
                            height: 48,
                            child: ElevatedButton(
                              onPressed: submitting
                                  ? null
                                  : () async {
                                      final double? acresVal = double.tryParse(acresController.text.trim());
                                      if (acresVal == null || acresVal <= 0 || acresVal > 10000) {
                                        setModalState(() => modalError = 'Please enter a valid acreage (1 to 10,000 acres).');
                                        return;
                                      }

                                      final cropTypeToRegister = selectedType == 'Other'
                                          ? customTypeController.text.trim()
                                          : selectedType;
                                      if (cropTypeToRegister.isEmpty) {
                                        setModalState(() => modalError = 'Please specify a crop variety.');
                                        return;
                                      }

                                      final sowingDateStr = sowingDateController.text.trim();
                                      if (sowingDateStr.isEmpty) {
                                        setModalState(() => modalError = 'Please select a sowing date.');
                                        return;
                                      }

                                       _farmerId ??= (await _repository.getFarmerId())?.toString();

                                      if (_farmerId == null) {
                                        setModalState(() => modalError = 'Authentication required. Please log in to add crops.');
                                        return;
                                      }

                                      final cropRegisteredMsg = context.tr('cropRegistered');

                                      setModalState(() {
                                        submitting = true;
                                        modalError = null;
                                      });

                                      try {
                                        await _repository.addCrop(
                                          farmerId: _farmerId!,
                                          cropType: cropTypeToRegister,
                                          acres: acresVal,
                                          sowingDate: sowingDateStr,
                                          location: locationController.text.trim(),
                                          status: 'Sowing',
                                        );

                                        if (mounted) {
                                          Navigator.pop(modalContext);
                                          ScaffoldMessenger.of(context).showSnackBar(
                                            SnackBar(
                                              content: Text(cropRegisteredMsg),
                                              backgroundColor: const Color(0xFF28653F),
                                            ),
                                          );
                                          _loadData();
                                        }
                                      } catch (err) {
                                        setModalState(() {
                                          submitting = false;
                                          modalError = 'Failed to add crop: ${err.toString().replaceAll('Exception: ', '').replaceAll('PostgresException(message: ', '')}';
                                        });
                                      }
                                    },
                              style: ElevatedButton.styleFrom(
                                backgroundColor: const Color(0xFF28653F),
                                foregroundColor: Colors.white,
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                                elevation: 2,
                              ),
                              child: submitting
                                  ? const SizedBox(
                                      width: 20,
                                      height: 20,
                                      child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
                                    )
                                  : Text(
                                      context.tr('saveCrop'),
                                      style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w800),
                                    ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  void _showEditCropModal(Map<String, dynamic> crop) {
    final acresController = TextEditingController(
      text: ((crop['acres'] as num?)?.toDouble() ?? 1.0).toString(),
    );
    final locationController = TextEditingController(
      text: crop['notes'] as String? ?? '',
    );
    final initialComment = (crop['farmer_comment'] ?? crop['comment']) as String? ?? '';
    final commentController = TextEditingController(text: initialComment);
    String currentStatus = (crop['status'] as String? ?? 'Sowing');
    bool updating = false;
    String? modalError;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(26)),
      ),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (modalCtx, setModalState) {
            return Padding(
              padding: EdgeInsets.only(
                left: 20,
                right: 20,
                top: 20,
                bottom: MediaQuery.of(modalCtx).viewInsets.bottom + 24,
              ),
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          '${context.tr('editCrop')} - ${crop['crop_name'] ?? crop['crop_type']}',
                          style: const TextStyle(
                            fontFamily: 'Fraunces',
                            fontSize: 18,
                            fontWeight: FontWeight.w800,
                            color: Color(0xFF15302A),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.close, color: Color(0xFF64748B)),
                          onPressed: () => Navigator.pop(modalCtx),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),

                    if (modalError != null) ...[
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        decoration: BoxDecoration(
                          color: const Color(0xFFFEE2E2),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: const Color(0xFFFCA5A5)),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.error_outline, color: Color(0xFF991B1B), size: 18),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                modalError!,
                                style: const TextStyle(color: Color(0xFF991B1B), fontSize: 12.5, fontWeight: FontWeight.w700),
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 14),
                    ],

                    Text(
                      '${context.tr('acres')} *',
                      style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: acresController,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: InputDecoration(
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),

                    const SizedBox(height: 14),
                    const Text(
                      'Growth Stage *',
                      style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    DropdownButtonFormField<String>(
                      value: kGrowthStages.contains(currentStatus) ? currentStatus : 'Sowing',
                      decoration: InputDecoration(
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                      items: kGrowthStages
                          .map((st) => DropdownMenuItem(value: st, child: Text(st)))
                          .toList(),
                      onChanged: (val) {
                        if (val != null) {
                          setModalState(() => currentStatus = val);
                        }
                      },
                    ),

                    const SizedBox(height: 14),
                    Text(
                      '${context.tr('plotLocation')} (Optional)',
                      style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: locationController,
                      decoration: InputDecoration(
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),

                    const SizedBox(height: 14),
                    const Text(
                      'Farmer Comment / Update Note (Optional)',
                      style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: commentController,
                      maxLines: 2,
                      decoration: InputDecoration(
                        hintText: 'e.g. Harvesting started today. Crop is ready for collection.',
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),

                    const SizedBox(height: 20),
                    SizedBox(
                      width: double.infinity,
                      height: 48,
                      child: ElevatedButton(
                        onPressed: updating
                            ? null
                            : () async {
                                final acres = double.tryParse(acresController.text.trim()) ?? 1.0;
                                final commentText = commentController.text.trim();
                                setModalState(() {
                                  updating = true;
                                  modalError = null;
                                });
                                 try {
                                  // Build payload — omit farmer_comment when empty.
                                  // Backend rejects empty string with 422: "comment cannot
                                  // be empty or whitespace only". Empty field = "keep existing".
                                  final Map<String, dynamic> updatePayload = {
                                    'acres': acres,
                                    'status': currentStatus,
                                    'notes': locationController.text.trim(),
                                    if (commentText.isNotEmpty) 'farmer_comment': commentText,
                                  };
                                  await _repository.updateCrop(
                                    cropId: crop['id'],
                                    updates: updatePayload,
                                  );
                                  if (mounted) {
                                    crop['acres'] = acres;
                                    crop['status'] = currentStatus;
                                    crop['stage'] = currentStatus;
                                    crop['notes'] = locationController.text.trim();
                                    if (commentText.isNotEmpty) crop['farmer_comment'] = commentText;
                                    Navigator.pop(modalCtx);
                                    ScaffoldMessenger.of(context).showSnackBar(
                                      SnackBar(content: Text(context.tr('cropUpdated'))),
                                    );
                                    _loadData();
                                  }

                                } catch (e) {
                                  String errorMsg = 'Failed to update crop field.';
                                  if (e is DioException) {
                                    final data = e.response?.data;
                                    if (data is Map) {
                                      errorMsg = data['message'] as String? ?? data['detail']?.toString() ?? errorMsg;
                                    }
                                  } else {
                                    errorMsg = e.toString();
                                  }
                                  setModalState(() {
                                    updating = false;
                                    modalError = errorMsg;
                                  });
                                }
                              },
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF28653F),
                          foregroundColor: Colors.white,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        ),
                        child: updating
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
                              )
                            : const Text('Update Crop', style: TextStyle(fontWeight: FontWeight.w800)),
                      ),
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  void _confirmDeleteCrop(Map<String, dynamic> crop) {
    final reasonController = TextEditingController();
    String? reasonError;

    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        bool deleting = false;
        return StatefulBuilder(
          builder: (dialogCtx, setDialogState) => AlertDialog(
            title: Text(context.tr('deleteCrop')),
            content: deleting
                ? const Row(
                    children: [
                      SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF28653F)),
                      ),
                      SizedBox(width: 16),
                      Text('Deleting crop...'),
                    ],
                  )
                : Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(context.tr('deleteCropConfirm')),
                      const SizedBox(height: 14),
                      const Text(
                        'Reason for deletion *',
                        style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
                      ),
                      const SizedBox(height: 6),
                      TextField(
                        controller: reasonController,
                        maxLines: 2,
                        decoration: InputDecoration(
                          hintText: 'e.g. Crop damaged due to heavy rainfall.',
                          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                          errorText: reasonError,
                        ),
                      ),
                    ],
                  ),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
            actions: deleting
                ? []
                : [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: Text(context.tr('cancel')),
                    ),
                    ElevatedButton(
                      onPressed: () async {
                        final reason = reasonController.text.trim();
                        if (reason.length < 3) {
                          setDialogState(() => reasonError = 'Reason for deletion must be at least 3 characters.');
                          return;
                        }
                        setDialogState(() {
                          reasonError = null;
                          deleting = true;
                        });
                        try {
                          await _repository.deleteCrop(crop['id'], reason: reason);
                          if (mounted) {
                            Navigator.pop(ctx);
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text(context.tr('cropDeleted'))),
                            );
                            setState(() {
                              _crops.removeWhere((c) => c['id']?.toString() == crop['id']?.toString());
                            });
                            _loadData();
                          }
                        } catch (e) {
                          final errorMsg = e.toString().replaceAll('Exception: ', '').replaceAll('ApiException: ', '').trim();
                          setDialogState(() {
                            deleting = false;
                            reasonError = 'Failed to delete crop. $errorMsg';
                          });
                        }
                      },
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFFDC2626),
                        foregroundColor: Colors.white,
                      ),
                      child: Text(context.tr('delete')),
                    ),
                  ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      color: const Color(0xFF28653F),
      onRefresh: _loadData,
      child: SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. Screen Header matching FarmerFields.jsx
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        context.tr('cropsAndCycles'),
                        style: const TextStyle(
                          fontFamily: 'Fraunces',
                          fontSize: 24,
                          fontWeight: FontWeight.w800,
                          color: Color(0xFF15302A),
                          letterSpacing: -0.5,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        context.tr('manageCropRegistrations'),
                        style: const TextStyle(
                          fontSize: 12.5,
                          color: Color(0xFF64748B),
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ),
                ElevatedButton.icon(
                  onPressed: _showAddCropModal,
                  icon: const Icon(Icons.add, size: 18, color: Colors.white),
                  label: Text(
                    context.tr('addCropBtn'),
                    style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w800, color: Colors.white),
                  ),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF15302A),
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                    elevation: 3,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // 2. Registered Landholding Banner matching FarmerFields.jsx
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0xFF15302A), Color(0xFF1F4438)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(20),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x2615302A),
                    blurRadius: 24,
                    offset: Offset(0, 8),
                  ),
                ],
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        context.tr('registeredLandholding'),
                        style: const TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 1,
                          color: Color(0xFF7BC79A),
                        ),
                      ),
                      const SizedBox(height: 4),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.baseline,
                        textBaseline: TextBaseline.alphabetic,
                        children: [
                          Text(
                            _totalAcres.toStringAsFixed(1),
                            style: const TextStyle(
                              fontFamily: 'Fraunces',
                              fontSize: 26,
                              fontWeight: FontWeight.w800,
                              color: Colors.white,
                            ),
                          ),
                          const SizedBox(width: 6),
                          Text(
                            context.tr('acresTotal'),
                            style: const TextStyle(
                              fontSize: 13.5,
                              color: Color(0xD9FFFFFF),
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '${_crops.length} ${context.tr('activeFieldPlots')}',
                        style: const TextStyle(
                          fontSize: 12,
                          color: Color(0xB3FFFFFF),
                        ),
                      ),
                    ],
                  ),
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.12),
                      shape: BoxShape.circle,
                    ),
                    child: const Center(
                      child: Text('🌾', style: TextStyle(fontSize: 24)),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Filter Chips
            if (_crops.isNotEmpty) ...[
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    'All',
                    'Cotton',
                    'Rice',
                    'Maize',
                    'Groundnut',
                    'Wheat',
                    'Chilli',
                  ].map((filter) {
                    final isSelected = _selectedFilter == filter;
                    return Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: FilterChip(
                        selected: isSelected,
                        label: Text(
                          filter == 'All' ? context.tr('allFilter') : filter,
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: isSelected ? FontWeight.w800 : FontWeight.w600,
                            color: isSelected ? Colors.white : const Color(0xFF334155),
                          ),
                        ),
                        backgroundColor: const Color(0xFFF1F5F9),
                        selectedColor: const Color(0xFF15302A),
                        checkmarkColor: Colors.white,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        onSelected: (_) {
                          setState(() {
                            _selectedFilter = filter;
                            _activeInitialCropId = null;
                          });
                        },
                      ),
                    );
                  }).toList(),
                ),
              ),
              const SizedBox(height: 16),
            ],

            // Error Banner
            if (_errorMessage != null)
              Container(
                margin: const EdgeInsets.only(bottom: 16),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  color: const Color(0xFFFEE2E2),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Expanded(
                      child: Text(
                        _errorMessage!,
                        style: const TextStyle(color: Color(0xFF991B1B), fontSize: 13, fontWeight: FontWeight.w700),
                      ),
                    ),
                    ElevatedButton(
                      onPressed: (_isLoading || _isRetrying) ? null : _loadData,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF991B1B),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        minimumSize: Size.zero,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                      ),
                      child: _isRetrying
                          ? const SizedBox(
                              width: 12,
                              height: 12,
                              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                            )
                          : const Text('Retry', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w800)),
                    ),
                  ],
                ),
              ),

            // 3. Crops List / Empty / Loading
            if (_isLoading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 48),
                child: Center(
                  child: Column(
                    children: [
                      CircularProgressIndicator(color: Color(0xFF28653F)),
                      SizedBox(height: 14),
                      Text(
                        'Fetching crops from database...',
                        style: TextStyle(color: Color(0xFF64748B), fontSize: 13, fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                ),
              )
            else if (_filteredCrops.isEmpty)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 36),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(22),
                  border: Border.all(color: const Color(0xFFCBD5E1), width: 1.5),
                  boxShadow: const [
                    BoxShadow(color: Color(0x05000000), blurRadius: 16, offset: Offset(0, 4)),
                  ],
                ),
                child: Column(
                  children: [
                    const Text('🌱', style: TextStyle(fontSize: 42)),
                    const SizedBox(height: 10),
                    Text(
                      context.tr('noCropsYet'),
                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: Color(0xFF15302A)),
                    ),
                    const SizedBox(height: 6),
                    SizedBox(
                      width: 270,
                      child: Text(
                        context.tr('registerFirstCrop'),
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 13, color: Color(0xFF64748B), height: 1.4),
                      ),
                    ),
                    const SizedBox(height: 18),
                    ElevatedButton.icon(
                      onPressed: _showAddCropModal,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF15302A),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 2,
                      ),
                      icon: const Icon(Icons.add, size: 18, color: Colors.white),
                      label: Text(
                        context.tr('addCropBtn'),
                        style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w800),
                      ),
                    ),
                  ],
                ),
              )
            else
              ListView.separated(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: _filteredCrops.length,
                separatorBuilder: (ctx, idx) => const SizedBox(height: 12),
                itemBuilder: (ctx, idx) {
                  final crop = _filteredCrops[idx];
                  return _buildCropCard(crop);
                },
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildCropCard(Map<String, dynamic> crop) {
    final cropType = crop['crop_type'] as String? ?? 'Crop';
    final cropName = crop['crop_name'] as String? ?? cropType;
    final acres = ((crop['acres'] as num?)?.toDouble() ?? 0.0).toStringAsFixed(1);
    final status = (crop['status'] as String? ?? 'Sowing');
    final activeStageIndex = getStageIndex(status);
    final sowingDate = crop['sowing_date'] as String? ?? 'N/A';
    final notes = crop['notes'] as String?;

    // Filter farm visits matching this crop
    final cropVisits = _visits.where((v) {
      return v['crop_id'] == crop['id'];
    }).toList();

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: const Color(0xFFE2E8F0)),
        boxShadow: const [
          BoxShadow(color: Color(0x08000000), blurRadius: 16, offset: Offset(0, 4)),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header Row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Container(
                    width: 46,
                    height: 46,
                    decoration: BoxDecoration(
                      color: const Color(0xFFE7F4EB),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Center(
                      child: Text(getCropEmoji(cropType), style: const TextStyle(fontSize: 24)),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        cropName,
                        style: const TextStyle(
                          fontFamily: 'Fraunces',
                          fontSize: 17,
                          fontWeight: FontWeight.w800,
                          color: Color(0xFF1E293B),
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '$acres acres',
                        style: const TextStyle(fontSize: 13, color: Color(0xFF64748B), fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                ],
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                decoration: BoxDecoration(
                  color: const Color(0xFFE7F4EB),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  status.toLowerCase(),
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w800,
                    color: Color(0xFF166534),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),

          // Sowed Date
          Row(
            children: [
              const Text('📅', style: TextStyle(fontSize: 12)),
              const SizedBox(width: 6),
              Text(
                '${context.tr('sowed')}: ',
                style: const TextStyle(fontSize: 12.5, color: Color(0xFF64748B), fontWeight: FontWeight.w600),
              ),
              Text(
                sowingDate,
                style: const TextStyle(fontSize: 12.5, color: Color(0xFF1E293B), fontWeight: FontWeight.w700),
              ),
            ],
          ),
          const SizedBox(height: 14),

          // 4-Stage Growth Cycle Timeline matching FarmerFields.jsx
          _buildStageTimeline(crop, activeStageIndex),
          const SizedBox(height: 14),

          // Farm Visits Section
          if (cropVisits.isNotEmpty) ...[
            const Divider(height: 1, color: Color(0xFFF1F5F9)),
            const SizedBox(height: 12),
            Text(
              context.tr('farmVisits'),
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: Color(0xFF475569)),
            ),
            const SizedBox(height: 8),
            ...cropVisits.map((v) {
              final isDone = v['status'] == 'completed';
              final visitMonth = v['visit_month'];
              final note = v['notes'] as String? ?? 'Farm Inspection';
              final label = visitMonth != null ? 'Month $visitMonth Visit' : note;
              final rawDate = (v['scheduled_date'] as String?) ?? (v['scheduledDate'] as String?) ?? (v['visit_date'] as String?);
              final dateText = _formatScheduledDate(rawDate);

              return Container(
                margin: const EdgeInsets.only(bottom: 6),
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: const Color(0xFFF8FAFC),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Container(
                          width: 22,
                          height: 22,
                          decoration: BoxDecoration(
                            color: isDone ? const Color(0xFFDCFCE7) : const Color(0xFFFEF3C7),
                            shape: BoxShape.circle,
                          ),
                          child: Center(
                            child: Text(
                              isDone ? '✓' : '!',
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w900,
                                color: isDone ? const Color(0xFF166534) : const Color(0xFFD97706),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              label,
                              style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Color(0xFF334155)),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              dateText == 'Not scheduled yet' ? dateText : 'Scheduled: $dateText',
                              style: TextStyle(
                                fontSize: 11,
                                color: dateText == 'Not scheduled yet' ? const Color(0xFF94A3B8) : const Color(0xFF475569),
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                      decoration: BoxDecoration(
                        color: isDone ? const Color(0xFFDCFCE7) : const Color(0xFFFEF3C7),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        (v['status'] as String? ?? 'scheduled').toLowerCase(),
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w800,
                          color: isDone ? const Color(0xFF166534) : const Color(0xFF92400E),
                        ),
                      ),
                    ),
                  ],
                ),
              );
            }),
            const SizedBox(height: 8),
          ],

          // Plot Identifier
          if (notes != null && notes.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(
                '📍 $notes',
                style: const TextStyle(fontSize: 12, fontStyle: FontStyle.italic, color: Color(0xFF64748B)),
              ),
            ),

          // Action Toolbar: Edit, Delete
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              IconButton(
                icon: const Icon(Icons.edit_outlined, size: 18, color: Color(0xFF64748B)),
                onPressed: () => _showEditCropModal(crop),
                tooltip: context.tr('editCrop'),
              ),
              IconButton(
                icon: const Icon(Icons.delete_outline, size: 18, color: Color(0xFFDC2626)),
                onPressed: () => _confirmDeleteCrop(crop),
                tooltip: context.tr('deleteCrop'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStageTimeline(Map<String, dynamic> crop, int activeStageIndex) {
    return Row(
      children: List.generate(kGrowthStages.length * 2 - 1, (index) {
        if (index.isOdd) {
          final stepIdx = index ~/ 2;
          final isPassed = stepIdx < activeStageIndex;
          return Expanded(
            child: Container(
              height: 3,
              margin: const EdgeInsets.only(bottom: 22),
              decoration: BoxDecoration(
                color: isPassed ? const Color(0xFF28653F) : const Color(0xFFE2E8F0),
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          );
        }

        final stepIdx = index ~/ 2;
        final isCompleted = stepIdx < activeStageIndex;
        final isActive = stepIdx == activeStageIndex;
        final label = kGrowthStages[stepIdx];

        Color circleBg = Colors.white;
        Color circleBorder = const Color(0xFFCBD5E1);
        Color textColor = const Color(0xFF94A3B8);

        if (isCompleted) {
          circleBg = const Color(0xFF28653F);
          circleBorder = const Color(0xFF28653F);
          textColor = Colors.white;
        } else if (isActive) {
          circleBg = const Color(0xFFF59E0B);
          circleBorder = const Color(0xFFF59E0B);
          textColor = Colors.white;
        }

        return GestureDetector(
          onTap: () async {
            // Allow farmer to advance or set stage
            try {
              await _repository.updateCrop(
                cropId: crop['id'],
                updates: {'status': label},
              );
              if (mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('${crop['crop_name'] ?? 'Crop'} stage set to $label')),
                );
                _loadData();
              }
            } catch (e) {
              if (mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Failed to update stage: $e')),
                );
              }
            }
          },
          child: Column(
            children: [
              Container(
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: circleBg,
                  border: Border.all(color: circleBorder, width: 2),
                  boxShadow: isActive
                      ? [
                          BoxShadow(
                            color: const Color(0xFFF59E0B).withOpacity(0.25),
                            blurRadius: 6,
                            spreadRadius: 2,
                          )
                        ]
                      : null,
                ),
                child: Center(
                  child: Text(
                    isCompleted ? '✓' : '${stepIdx + 1}',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                      color: textColor,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 6),
              Text(
                label,
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: isActive || isCompleted ? FontWeight.w800 : FontWeight.w600,
                  color: isActive
                      ? const Color(0xFFD97706)
                      : isCompleted
                          ? const Color(0xFF28653F)
                          : const Color(0xFF94A3B8),
                ),
              ),
            ],
          ),
        );
      }),
    );
  }
}
