import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:intl/intl.dart';

import '../../../core/constants/app_colors.dart';
import '../../../core/localization/app_localizations.dart';
import '../../../core/services/connectivity_service.dart';
import '../../../core/widgets/warehouse_icon.dart';
import '../../../data/repositories/farmer_repository.dart';

String formatQtl(dynamic kg) {
  if (kg == null) return '0 Qtl';
  final doubleVal = double.tryParse(kg.toString()) ?? 0.0;
  final qtl = (doubleVal / 100).round();
  return '$qtl Qtl';
}

({String day, String monthYear}) formatDateParts(String? dateStr) {
  if (dateStr == null || dateStr.isEmpty) {
    return (day: '01', monthYear: 'Jan, 2026');
  }
  try {
    final d = DateTime.parse(dateStr);
    final day = d.day.toString().padLeft(2, '0');
    final month = DateFormat('MMM').format(d);
    final year = d.year;
    return (day: day, monthYear: '$month, $year');
  } catch (_) {
    return (day: '01', monthYear: dateStr);
  }
}

String getGrainEmoji(String? type) {
  if (type == null) return '🌾';
  final t = type.toLowerCase();
  if (t.contains('cotton') || t.contains('kapus')) return '🌸';
  if (t.contains('rice') || t.contains('paddy')) return '🌾';
  if (t.contains('maize') || t.contains('corn')) return '🌽';
  if (t.contains('groundnut')) return '🥜';
  if (t.contains('wheat')) return '🌾';
  if (t.contains('sugarcane')) return '🎋';
  if (t.contains('bajra') || t.contains('millet')) return '🌾';
  if (t.contains('soybean') || t.contains('gram') || t.contains('pulses')) return '🫘';
  return '🌾';
}

/// Returns the local asset path for a grain type string.
/// Uses the same image library as the seed catalog (SSS-001).
String getGrainAsset(String? type) {
  if (type == null) return 'assets/images/paddy-seeds.jpg';
  final t = type.toLowerCase();
  if (t.contains('cotton') || t.contains('kapus')) return 'assets/images/cotton.png';
  if (t.contains('rice') || t.contains('paddy')) return 'assets/images/paddy-seeds.jpg';
  if (t.contains('maize') || t.contains('yellow maize')) return 'assets/images/maize.png';
  if (t.contains('corn') && !t.contains('maize')) return 'assets/images/sweet-corn-seeds.jpg';
  if (t.contains('groundnut')) return 'assets/images/groundnut.png';
  if (t.contains('wheat')) return 'assets/images/wheat-seeds.jpg';
  if (t.contains('sugarcane')) return 'assets/images/sugarcane.jpg';
  if (t.contains('bajra') || t.contains('millet')) return 'assets/images/bajra.png';
  if (t.contains('soybean') || t.contains('gram') || t.contains('pulses')) return 'assets/images/soybean-seeds.png';
  if (t.contains('barley')) return 'assets/images/barley-seeds.jpg';
  // Default: paddy – a real grain, never the app logo or onboarding screenshot
  return 'assets/images/paddy-seeds.jpg';
}

class GrainSalesScreen extends StatefulWidget {
  const GrainSalesScreen({super.key});

  @override
  State<GrainSalesScreen> createState() => GrainSalesScreenState();
}

class GrainSalesScreenState extends State<GrainSalesScreen> with WidgetsBindingObserver {
  final _repository = FarmerRepository();
  final _searchController = TextEditingController();

  List<Map<String, dynamic>> _warehouses = [];
  List<Map<String, dynamic>> _bookings = [];
  List<Map<String, dynamic>> _marketRates = [];

  bool _isLoading = true;
  bool _isRetrying = false;
  String? _errorMessage;
  String? _farmerId;

  String _searchTerm = '';
  bool _showAllBookings = false;

  // Warehouse Filter state
  String? _filterDistrict;
  double? _minCapacityKg;
  bool _filterAvailableOnly = false;

  bool get _hasWarehouseFilters =>
      (_filterDistrict != null && _filterDistrict != 'All') ||
      (_minCapacityKg != null && _minCapacityKg! > 0) ||
      _filterAvailableOnly;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    ConnectivityService().addOnRestoredListener(_onConnectivityRestored);
    _loadData();
  }

  @override
  void dispose() {
    ConnectivityService().removeOnRestoredListener(_onConnectivityRestored);
    WidgetsBinding.instance.removeObserver(this);
    _searchController.dispose();
    super.dispose();
  }

  void _onConnectivityRestored() {
    // When internet returns, clear stale connectivity error banner to restore normal UI.
    // Do not automatically hammer the backend; wait for user Pull-to-Refresh or Retry.
    if (mounted && _errorMessage != null) {
      setState(() {
        _errorMessage = null;
      });
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _warehouses.isEmpty) {
      _loadData();
    }
  }

  Future<void> refreshData() => _loadData();

  Future<void> _loadData() async {
    if (_isRetrying) return;
    if (_warehouses.isEmpty) {
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
      final profileFuture = _repository.getFarmerProfile();
      final warehousesFuture = _repository.getWarehouses();
      final bookingsFuture = _repository.getBookingSlots().catchError((_) => <Map<String, dynamic>>[]);
      final marketRatesFuture = _repository.getMarketRates().catchError((_) => <Map<String, dynamic>>[]);

      final results = await Future.wait([
        profileFuture,
        warehousesFuture,
        bookingsFuture,
        marketRatesFuture,
      ]);

      final profile = results[0] as Map<String, dynamic>?;
      final warehousesData = results[1] as List<Map<String, dynamic>>;
      final bookingsData = results[2] as List<Map<String, dynamic>>;
      final marketRatesData = results[3] as List<Map<String, dynamic>>;

      if (profile != null && profile['farmer_id'] != null) {
        _farmerId = profile['farmer_id']?.toString();
      }

      if (mounted) {
        setState(() {
          _warehouses = warehousesData;
          _bookings = bookingsData;
          _marketRates = marketRatesData;
          _isLoading = false;
          _isRetrying = false;
          _errorMessage = null; // Stale error cleared on success!
        });
      }
    } catch (err) {
      if (mounted) {
        setState(() {
          _errorMessage = context.tr('errorLoad');
          _isLoading = false;
          _isRetrying = false;
        });
      }
    }
  }

  List<Map<String, dynamic>> get _filteredWarehouses {
    return _warehouses.where((w) {
      final name = (w['name'] as String? ?? '').toLowerCase();
      final address = (w['address'] as String? ?? '').toLowerCase();
      final district = (w['district'] as String? ?? '').toLowerCase();
      final term = _searchTerm.toLowerCase().trim();

      if (term.isNotEmpty && !name.contains(term) && !address.contains(term) && !district.contains(term)) {
        return false;
      }
      if (_filterDistrict != null && _filterDistrict!.isNotEmpty && _filterDistrict != 'All') {
        if (!district.contains(_filterDistrict!.toLowerCase()) && !address.contains(_filterDistrict!.toLowerCase())) {
          return false;
        }
      }
      if (_filterAvailableOnly) {
        final totalKg = (w['total_capacity_kg'] as num?)?.toDouble() ?? 500000.0;
        final usedKg = (w['current_load_kg'] as num?)?.toDouble() ?? 0.0;
        if (totalKg - usedKg <= 0) return false;
      }
      if (_minCapacityKg != null && _minCapacityKg! > 0) {
        final totalKg = (w['total_capacity_kg'] as num?)?.toDouble() ?? 500000.0;
        if (totalKg < _minCapacityKg!) return false;
      }
      return true;
    }).toList();
  }

  void _openBookingModal({dynamic warehouseId}) {
    if (_warehouses.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(context.tr('noWarehousesFound')),
          backgroundColor: AppColors.alert,
        ),
      );
      return;
    }

    dynamic selectedWhId = warehouseId ?? _warehouses.first['id'];
    String selectedGrain = 'Cotton (Kapus)';
    final qtyController = TextEditingController();
    final dateController = TextEditingController(
      text: DateTime.now().toIso8601String().split('T')[0],
    );
    final addressController = TextEditingController();
    bool isSubmitting = false;
    String? sheetError;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(26)),
      ),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (sheetContext, setSheetState) {

            final chosenWh = _warehouses.firstWhere(
              (w) => w['id'].toString() == selectedWhId.toString(),
              orElse: () => _warehouses.first,
            );
            final totalKg = (chosenWh['total_capacity_kg'] as num?)?.toDouble() ?? (chosenWh['capacity'] as num?)?.toDouble() ?? 100000.0;
            final usedKg = (chosenWh['current_load_kg'] as num?)?.toDouble() ?? 0.0;
            final availableKg = (totalKg - usedKg).clamp(0.0, totalKg);
            final availableQtl = (availableKg / 100).round();

            final requestedKg = double.tryParse(qtyController.text.trim()) ?? 0.0;
            final isQtyExceeded = requestedKg > availableKg;

            final hasActiveBooking = _bookings.any((b) {
              final bWhId = b['warehouse']?['id']?.toString() ?? b['warehouse_id']?.toString();
              final bDate = b['booking_date']?.toString();
              final bStatus = (b['status']?.toString() ?? '').toLowerCase();
              return bWhId == selectedWhId.toString() &&
                  bDate == dateController.text.trim() &&
                  (bStatus == 'pending' || bStatus == 'confirmed');
            });

            return SafeArea(
              top: true,
              child: Padding(
                padding: EdgeInsets.only(
                  left: 20,
                  right: 20,
                  top: 10,
                  bottom: MediaQuery.of(sheetContext).viewInsets.bottom + 24,
                ),
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Drag handle for smooth UX
                      Center(
                        child: Container(
                          width: 38,
                          height: 4,
                          margin: const EdgeInsets.only(bottom: 14),
                          decoration: BoxDecoration(
                            color: const Color(0xFFCBD5E1),
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                      ),

                      // Sheet Header
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  context.tr('bookDropOffSlot'),
                                  style: GoogleFonts.fraunces(
                                    fontSize: 20,
                                    fontWeight: FontWeight.w800,
                                    color: AppColors.forest,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  'Direct gate-in with no mandi queue',
                                  style: GoogleFonts.manrope(
                                    fontSize: 12,
                                    fontWeight: FontWeight.w600,
                                    color: const Color(0xFF64748B),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          InkWell(
                            onTap: () => Navigator.pop(sheetContext),
                            borderRadius: BorderRadius.circular(16),
                            child: Container(
                              width: 32,
                              height: 32,
                              decoration: const BoxDecoration(
                                color: Color(0xFFF1F5F9),
                                shape: BoxShape.circle,
                              ),
                              alignment: Alignment.center,
                              child: const Icon(Icons.close, size: 16, color: Color(0xFF64748B)),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 14),

                      // Sheet-level Error Banner
                      if (sheetError != null) ...[
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: const Color(0xFFFEF2F2),
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(color: const Color(0xFFFCA5A5), width: 1.5),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Icon(Icons.error_outline_rounded, color: Color(0xFFDC2626), size: 20),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  sheetError!,
                                  style: GoogleFonts.manrope(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w600,
                                    color: const Color(0xFF991B1B),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 14),
                      ],

                      // 1. Warehouse dropdown
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            '${context.tr('selectWarehouseLocation')} *',
                            style: GoogleFonts.manrope(
                              fontSize: 12.5,
                              fontWeight: FontWeight.w700,
                              color: const Color(0xFF475569),
                            ),
                          ),
                          Text(
                            'Available: $availableQtl Qtl',
                            style: GoogleFonts.manrope(
                              fontSize: 12,
                              fontWeight: FontWeight.w800,
                              color: availableKg > 0 ? AppColors.forest : const Color(0xFFDC2626),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        decoration: BoxDecoration(
                          border: Border.all(color: const Color(0xFFCBD5E1), width: 1.5),
                          borderRadius: BorderRadius.circular(14),
                          color: Colors.white,
                        ),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<dynamic>(
                            value: selectedWhId,
                            isExpanded: true,
                            style: GoogleFonts.manrope(
                              fontSize: 14,
                              fontWeight: FontWeight.w700,
                              color: const Color(0xFF1E293B),
                            ),
                            items: _warehouses.map((w) {
                              final name = w['name'] ?? 'Warehouse';
                              final address = w['address'] ?? 'Kurnool Hub';
                              return DropdownMenuItem<dynamic>(
                                value: w['id'],
                                child: Text(
                                  '$name ($address)',
                                  overflow: TextOverflow.ellipsis,
                                ),
                              );
                            }).toList(),
                            onChanged: (val) {
                              if (val != null) {
                                setSheetState(() {
                                  selectedWhId = val;
                                  sheetError = null;
                                });
                              }
                            },
                          ),
                        ),
                      ),
                      const SizedBox(height: 14),

                      // 2. Grain crop type
                      Text(
                        '${context.tr('grainCropType')} *',
                        style: GoogleFonts.manrope(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: const Color(0xFF475569),
                        ),
                      ),
                      const SizedBox(height: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        decoration: BoxDecoration(
                          border: Border.all(color: const Color(0xFFCBD5E1), width: 1.5),
                          borderRadius: BorderRadius.circular(14),
                          color: Colors.white,
                        ),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<String>(
                            value: selectedGrain,
                            isExpanded: true,
                            style: GoogleFonts.manrope(
                              fontSize: 14,
                              fontWeight: FontWeight.w700,
                              color: const Color(0xFF1E293B),
                            ),
                            items: const [
                              DropdownMenuItem(value: 'Cotton (Kapus)', child: Text('🌸 Cotton (Kapus)')),
                              DropdownMenuItem(value: 'Paddy / Rice', child: Text('🌾 Paddy / Rice')),
                              DropdownMenuItem(value: 'Yellow Maize', child: Text('🌽 Yellow Maize')),
                              DropdownMenuItem(value: 'Groundnut (Pods)', child: Text('🥜 Groundnut (Pods)')),
                              DropdownMenuItem(value: 'Wheat', child: Text('🌾 Wheat')),
                              DropdownMenuItem(value: 'Sugarcane', child: Text('🎋 Sugarcane')),
                            ],
                            onChanged: (val) {
                              if (val != null) {
                                setSheetState(() {
                                  selectedGrain = val;
                                  sheetError = null;
                                });
                              }
                            },
                          ),
                        ),
                      ),
                      const SizedBox(height: 14),

                      // 3. Estimated Quantity
                      Text(
                        '${context.tr('estimatedQuantity')} *',
                        style: GoogleFonts.manrope(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: const Color(0xFF475569),
                        ),
                      ),
                      const SizedBox(height: 6),
                      TextField(
                        controller: qtyController,
                        keyboardType: TextInputType.number,
                        style: GoogleFonts.manrope(fontSize: 14, fontWeight: FontWeight.w600),
                        onChanged: (_) => setSheetState(() => sheetError = null),
                        decoration: InputDecoration(
                          hintText: 'Enter quantity in kg',
                          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: BorderSide(
                              color: isQtyExceeded ? const Color(0xFFDC2626) : const Color(0xFFCBD5E1),
                              width: 1.5,
                            ),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: BorderSide(
                              color: isQtyExceeded ? const Color(0xFFDC2626) : const Color(0xFFCBD5E1),
                              width: 1.5,
                            ),
                          ),
                        ),
                      ),
                      if (isQtyExceeded) ...[
                        const SizedBox(height: 4),
                        Text(
                          'Only $availableQtl Qtl (${availableKg.toStringAsFixed(0)} kg) currently available in this warehouse.',
                          style: GoogleFonts.manrope(fontSize: 11.5, fontWeight: FontWeight.w700, color: const Color(0xFFDC2626)),
                        ),
                      ],
                      const SizedBox(height: 14),

                      // 4. Delivery Date Slot
                      Text(
                        '${context.tr('deliveryDateSlot')} *',
                        style: GoogleFonts.manrope(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: const Color(0xFF475569),
                        ),
                      ),
                      const SizedBox(height: 6),
                      TextField(
                        controller: dateController,
                        readOnly: true,
                        style: GoogleFonts.manrope(fontSize: 14, fontWeight: FontWeight.w600),
                        onTap: () async {
                          final picked = await showDatePicker(
                            context: sheetContext,
                            initialDate: DateTime.now(),
                            firstDate: DateTime.now(),
                            lastDate: DateTime.now().add(const Duration(days: 90)),
                          );
                          if (picked != null) {
                            setSheetState(() {
                              dateController.text = picked.toIso8601String().split('T')[0];
                              sheetError = null;
                            });
                          }
                        },
                        decoration: InputDecoration(
                          suffixIcon: const Icon(Icons.calendar_today_rounded, size: 18, color: Color(0xFF64748B)),
                          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5),
                          ),
                        ),
                      ),
                      if (hasActiveBooking) ...[
                        const SizedBox(height: 4),
                        Text(
                          'You already have an active booking for this warehouse on ${dateController.text.trim()}.',
                          style: GoogleFonts.manrope(fontSize: 11.5, fontWeight: FontWeight.w700, color: const Color(0xFFD97706)),
                        ),
                      ],
                      const SizedBox(height: 14),

                      // 5. Origin Farm Address
                      Text(
                        '${context.tr('originFarmAddress')} *',
                        style: GoogleFonts.manrope(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: const Color(0xFF475569),
                        ),
                      ),
                      const SizedBox(height: 6),
                      TextField(
                        controller: addressController,
                        style: GoogleFonts.manrope(fontSize: 14, fontWeight: FontWeight.w600),
                        onChanged: (_) => setSheetState(() => sheetError = null),
                        decoration: InputDecoration(
                          hintText: 'e.g. Kalluru Farm, Plot #14',
                          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5),
                          ),
                        ),
                      ),
                      const SizedBox(height: 20),

                      // Submit Button
                      SizedBox(
                        width: double.infinity,
                        height: 50,
                        child: ElevatedButton(
                          style: ElevatedButton.styleFrom(
                            backgroundColor: AppColors.forest,
                            foregroundColor: Colors.white,
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                            elevation: 2,
                          ),
                          onPressed: isSubmitting
                              ? null
                              : () async {
                                  final rawQty = qtyController.text.trim();
                                  if (rawQty.isEmpty) {
                                    setSheetState(() => sheetError = 'Estimated quantity is required.');
                                    return;
                                  }

                                  final qty = double.tryParse(rawQty);
                                  if (qty == null || qty <= 0) {
                                    setSheetState(() => sheetError = 'Please enter a valid quantity in kg.');
                                    return;
                                  }

                                  if (qty > availableKg) {
                                    setSheetState(() => sheetError = 'Only $availableQtl Qtl (${availableKg.toStringAsFixed(0)} kg) currently available in this warehouse.');
                                    return;
                                  }

                                  final addr = addressController.text.trim();
                                  if (addr.isEmpty) {
                                    setSheetState(() => sheetError = 'Origin farm / village address is required.');
                                    return;
                                  }

                                  if (addr.length < 4) {
                                    setSheetState(() => sheetError = 'Origin farm / village address must be at least 4 characters.');
                                    return;
                                  }

                                  if (hasActiveBooking) {
                                    setSheetState(() => sheetError = 'You already have an active booking for this warehouse on ${dateController.text.trim()}.');
                                    return;
                                  }

                                  final fId = _farmerId;

                                  setSheetState(() {
                                    isSubmitting = true;
                                    sheetError = null;
                                  });
                                  try {
                                    final res = await _repository.bookDeliverySlot(
                                      farmerId: fId,
                                      warehouseId: selectedWhId,
                                      warehouseSlotId: null,
                                      grainType: selectedGrain,
                                      quantityKg: qty,
                                      bookingDate: dateController.text.trim(),
                                      deliveryAddress: addr,
                                    );

                                    if (!sheetContext.mounted) return;
                                    Navigator.pop(sheetContext);

                                    await _loadData();

                                    final bookingData = res['booking'] as Map<String, dynamic>?;
                                    final newBookingId = bookingData?['id']?.toString() ?? (1000 + DateTime.now().millisecond).toString();
                                    final rawStatus = (bookingData?['status'] as String? ?? 'pending').toLowerCase();
                                    final displayStatus = rawStatus.isNotEmpty
                                        ? rawStatus[0].toUpperCase() + rawStatus.substring(1)
                                        : 'Pending';

                                    final invoiceMap = {
                                      'title': 'Warehouse Drop-off (${selectedGrain.split(' ')[0]})',
                                      'invoiceNo': 'SLOT-$newBookingId',
                                      'date': dateController.text.trim(),
                                      'status': displayStatus,
                                      'item': '${qty.toStringAsFixed(0)} kg · $addr',
                                      'total': 'Delivery Pass Active',
                                      'warehouseName': chosenWh['name'] ?? 'Warehouse Hub',
                                    };

                                    if (mounted) {
                                      _showInvoiceModal(invoiceMap);
                                    }
                                  } catch (err) {
                                    final cleanMsg = err
                                        .toString()
                                        .replaceFirst('Exception: ', '')
                                        .replaceFirst('ApiException: ', '')
                                        .trim();
                                    setSheetState(() {
                                      isSubmitting = false;
                                      sheetError = cleanMsg.isNotEmpty ? cleanMsg : 'Booking failed. Please try again.';
                                    });
                                  } finally {
                                    if (sheetContext.mounted && isSubmitting) {
                                      setSheetState(() => isSubmitting = false);
                                    }
                                  }
                                },
                          child: isSubmitting
                              ? const SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                                )
                              : Text(
                                  context.tr('confirmDeliverySlot'),
                                  style: GoogleFonts.manrope(fontSize: 15, fontWeight: FontWeight.w800),
                                ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            );
          },
        );
      },
    );
  }

  void _showInvoiceModal(Map<String, dynamic> invoice) {
    showDialog(
      context: context,
      builder: (ctx) {
        return Dialog(
          backgroundColor: Colors.white,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 26),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Success Circle Icon
                Container(
                  width: 56,
                  height: 56,
                  decoration: const BoxDecoration(
                    color: Color(0xFFE7F4EB),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: const Icon(Icons.check_rounded, color: Color(0xFF28653F), size: 30),
                ),
                const SizedBox(height: 16),

                Text(
                  invoice['title'] ?? 'Warehouse Slot',
                  style: GoogleFonts.fraunces(
                    fontSize: 20,
                    fontWeight: FontWeight.w800,
                    color: AppColors.forest,
                  ),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 4),
                Text(
                  'Invoice #${invoice['invoiceNo']}',
                  style: GoogleFonts.manrope(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: const Color(0xFF64748B),
                  ),
                ),
                const SizedBox(height: 20),

                // Invoice Details Box
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFFF8FAF7),
                    borderRadius: BorderRadius.circular(18),
                    border: Border.all(color: const Color(0xFFE2E8F0)),
                  ),
                  child: Column(
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Date:', style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          Text(invoice['date'] ?? '', style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF1E293B), fontWeight: FontWeight.w800)),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Status:', style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          Text(invoice['status'] ?? 'Confirmed', style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF166534), fontWeight: FontWeight.w800)),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Item:', style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              invoice['item'] ?? '',
                              textAlign: TextAlign.right,
                              style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF1E293B), fontWeight: FontWeight.w800),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      const Divider(color: Color(0xFFE2E8F0), height: 1),
                      const SizedBox(height: 10),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Total Amount:', style: GoogleFonts.manrope(fontSize: 13, color: const Color(0xFF1E293B), fontWeight: FontWeight.w800)),
                          Text(
                            invoice['total'] ?? 'Delivery Pass Active',
                            style: GoogleFonts.fraunces(
                              fontSize: 15,
                              color: AppColors.forest,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),

                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.forest,
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      elevation: 0,
                    ),
                    onPressed: () => Navigator.pop(ctx),
                    child: Text(
                      context.tr('done'),
                      style: GoogleFonts.manrope(fontSize: 14.5, fontWeight: FontWeight.w800),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  void _showBookingDetailsModal(Map<String, dynamic> slot) {
    final invoiceMap = {
      'title': 'Warehouse Slot (${slot['grain_type'] ?? 'Grain'})',
      'invoiceNo': 'SLOT-${slot['id']}',
      'date': slot['booking_date'] ?? '',
      'status': (slot['status'] as String? ?? 'Confirmed').toUpperCase(),
      'item': '${slot['quantity_kg'] ?? 0} kg · ${slot['delivery_address'] ?? 'Kalluru Farm'}',
      'total': 'Delivery Pass Active',
      'warehouseName': slot['warehouses']?['name'] ?? 'Kurnool Warehouse Hub',
    };

    final isCancelable = slot['status'] == 'pending' || slot['status'] == 'confirmed';

    showDialog(
      context: context,
      builder: (ctx) {
        return Dialog(
          backgroundColor: Colors.white,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 52,
                  height: 52,
                  decoration: const BoxDecoration(
                    color: Color(0xFFE7F4EB),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: const Text('🚚', style: TextStyle(fontSize: 24)),
                ),
                const SizedBox(height: 12),
                Text(
                  invoiceMap['title'] ?? '',
                  style: GoogleFonts.fraunces(
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                    color: AppColors.forest,
                  ),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 4),
                Text(
                  'Invoice #${invoiceMap['invoiceNo']}',
                  style: GoogleFonts.manrope(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: const Color(0xFF64748B),
                  ),
                ),
                const SizedBox(height: 16),
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: const Color(0xFFF8FAF7),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: const Color(0xFFE2E8F0)),
                  ),
                  child: Column(
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Warehouse:', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          Text(invoiceMap['warehouseName'] ?? '', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF1E293B), fontWeight: FontWeight.w800)),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Date:', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          Text(invoiceMap['date'] ?? '', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF1E293B), fontWeight: FontWeight.w800)),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Status:', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          Text(invoiceMap['status'] ?? '', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF166534), fontWeight: FontWeight.w800)),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text('Quantity:', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF64748B), fontWeight: FontWeight.w600)),
                          Text('${slot['quantity_kg']} kg', style: GoogleFonts.manrope(fontSize: 12.5, color: const Color(0xFF1E293B), fontWeight: FontWeight.w800)),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 18),
                Row(
                  children: [
                    if (isCancelable) ...[
                      Expanded(
                        child: OutlinedButton(
                          style: OutlinedButton.styleFrom(
                            foregroundColor: const Color(0xFF991B1B),
                            side: const BorderSide(color: Color(0xFFFCA5A5)),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                            padding: const EdgeInsets.symmetric(vertical: 12),
                          ),
                          onPressed: () async {
                            final confirm = await showDialog<bool>(
                              context: ctx,
                              builder: (dCtx) => AlertDialog(
                                title: const Text('Cancel Booking'),
                                content: const Text('Are you sure you want to cancel this delivery slot?'),
                                actions: [
                                  TextButton(onPressed: () => Navigator.pop(dCtx, false), child: const Text('No')),
                                  TextButton(onPressed: () => Navigator.pop(dCtx, true), child: const Text('Yes, Cancel', style: TextStyle(color: Colors.red))),
                                ],
                              ),
                            );
                            if (confirm == true) {
                              if (!ctx.mounted) return;
                              Navigator.pop(ctx);
                              await _repository.cancelBookingSlot(slot['id']);
                              await _loadData();
                              if (mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(content: Text('Booking slot cancelled.')),
                                );
                              }
                            }
                          },
                          child: Text(
                            'Cancel Slot',
                            style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w700),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                    ],
                    Expanded(
                      child: ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppColors.forest,
                          foregroundColor: Colors.white,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                          padding: const EdgeInsets.symmetric(vertical: 12),
                        ),
                        onPressed: () => Navigator.pop(ctx),
                        child: Text(
                          context.tr('done'),
                          style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w800),
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
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAF7),
      body: RefreshIndicator(
        color: AppColors.canopy,
        onRefresh: _loadData,
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 1. Header & Primary CTA
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          context.tr('grainSlotBooking'),
                          style: GoogleFonts.fraunces(
                            fontSize: 22,
                            fontWeight: FontWeight.w800,
                            color: AppColors.forest,
                            letterSpacing: -0.5,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          context.tr('selectWarehouseSub'),
                          style: GoogleFonts.manrope(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: const Color(0xFF64748B),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.forest,
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                      elevation: 2,
                    ),
                    onPressed: () => _openBookingModal(),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.add, size: 16, color: Colors.white),
                        const SizedBox(width: 4),
                        Text(
                          context.tr('bookSlotBtn'),
                          style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w800),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),

              // Error banner if any
              if (_errorMessage != null) ...[
                Container(
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
                          style: GoogleFonts.manrope(
                            color: const Color(0xFF991B1B),
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      TextButton(
                        onPressed: (_isLoading || _isRetrying) ? null : _loadData,
                        style: TextButton.styleFrom(
                          backgroundColor: const Color(0xFF991B1B),
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                          minimumSize: Size.zero,
                          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
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
                const SizedBox(height: 16),
              ],

              // 2. Search Bar with Filter Icon
              Row(
                children: [
                    Expanded(
                      child: Container(
                        height: 46,
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: TextField(
                          controller: _searchController,
                          onChanged: (val) => setState(() => _searchTerm = val),
                          style: GoogleFonts.manrope(fontSize: 13.5, fontWeight: FontWeight.w600, color: const Color(0xFF1E293B)),
                          decoration: InputDecoration(
                            hintText: context.tr('searchWarehousePlaceholder'),
                            hintStyle: GoogleFonts.manrope(color: const Color(0xFF94A3B8), fontSize: 13.5),
                            prefixIcon: const Icon(Icons.search, size: 18, color: Color(0xFF94A3B8)),
                            contentPadding: const EdgeInsets.symmetric(vertical: 12),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(14),
                              borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5),
                            ),
                            enabledBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(14),
                              borderSide: const BorderSide(color: Color(0xFFCBD5E1), width: 1.5),
                            ),
                            focusedBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(14),
                              borderSide: const BorderSide(color: Color(0xFF15302A), width: 1.5),
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    InkWell(
                      onTap: _showWarehouseFilterSheet,
                      borderRadius: BorderRadius.circular(14),
                      child: Container(
                        width: 46,
                        height: 46,
                        decoration: BoxDecoration(
                          color: _hasWarehouseFilters ? const Color(0xFFE7F4EB) : Colors.white,
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(
                            color: _hasWarehouseFilters ? AppColors.canopy : const Color(0xFFCBD5E1),
                            width: 1.5,
                          ),
                        ),
                        alignment: Alignment.center,
                        child: Icon(
                          Icons.filter_list_rounded,
                          size: 22,
                          color: _hasWarehouseFilters ? AppColors.canopy : const Color(0xFF475569),
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 20),

              // 3. Available Warehouses Section
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    context.tr('availableWarehouses'),
                    style: GoogleFonts.fraunces(
                      fontSize: 16,
                      fontWeight: FontWeight.w800,
                      color: AppColors.forest,
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                    decoration: BoxDecoration(
                      color: const Color(0xFFE7F4EB),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '${_filteredWarehouses.length} Available',
                      style: GoogleFonts.manrope(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w800,
                        color: const Color(0xFF28653F),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),

              if (_isLoading)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 32),
                  child: Center(
                    child: CircularProgressIndicator(strokeWidth: 3, color: Color(0xFF7BC79A)),
                  ),
                )
              else if (_filteredWarehouses.isEmpty)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(18),
                    border: Border.all(color: const Color(0xFFCBD5E1), width: 1.5),
                  ),
                  child: Center(
                    child: Text(
                      context.tr('noWarehousesFound'),
                      style: GoogleFonts.manrope(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: const Color(0xFF64748B),
                      ),
                      textAlign: TextAlign.center,
                    ),
                  ),
                )
              else
                ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: _filteredWarehouses.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 14),
                  itemBuilder: (ctx, index) {
                    final wh = _filteredWarehouses[index];
                    final totalKg = (wh['total_capacity_kg'] as num?)?.toDouble() ?? (wh['capacity'] as num?)?.toDouble() ?? 500000.0;
                    final availKg = (wh['available_capacity'] as num?)?.toDouble() ?? (wh['available'] as num?)?.toDouble() ?? totalKg;
                    final usedKg = (wh['current_load_kg'] as num?)?.toDouble() ?? (totalKg - availKg).clamp(0.0, totalKg);

                    final totalQtl = (totalKg / 100).round();
                    final availQtl = (availKg / 100).round();
                    final usedQtl = (usedKg / 100).round();

                    final availRatio = totalKg > 0 ? (availKg / totalKg) : 0.0;
                    final isHighCap = availQtl >= 1000 || availRatio >= 0.4;
                    final isMedCap = availQtl > 0 && !isHighCap;

                    final tagText = wh['status'] as String? ??
                        (isHighCap
                            ? context.tr('highCapacity')
                            : isMedCap
                                ? context.tr('mediumCapacity')
                                : context.tr('lowCapacity'));

                    final tagColor = isHighCap
                        ? const Color(0xFF166534)
                        : isMedCap
                            ? const Color(0xFF92400E)
                            : const Color(0xFF64748B);

                    final tagBg = isHighCap
                        ? const Color(0xFFE7F4EB)
                        : isMedCap
                            ? const Color(0xFFFEF3C7)
                            : const Color(0xFFF1F5F9);

                    final progressRatio = totalKg > 0 ? (usedKg / totalKg).clamp(0.0, 1.0) : 0.0;

                    return InkWell(
                      borderRadius: BorderRadius.circular(20),
                      onTap: () => _openBookingModal(warehouseId: wh['id']),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(color: const Color(0xFFE2E8F0)),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x08000000),
                              blurRadius: 16,
                              offset: Offset(0, 4),
                            ),
                          ],
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            // Top Row
                            Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Expanded(
                                  child: Row(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      WarehouseIcon(
                                        size: 24,
                                        bgColor: isHighCap ? const Color(0xFFE7F4EB) : const Color(0xFFFEF3C7),
                                      ),
                                      const SizedBox(width: 12),
                                      Expanded(
                                        child: Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              wh['name'] ?? 'Warehouse',
                                              style: GoogleFonts.fraunces(
                                                fontSize: 15.5,
                                                fontWeight: FontWeight.w800,
                                                color: const Color(0xFF1E293B),
                                              ),
                                            ),
                                            const SizedBox(height: 2),
                                            Text(
                                              '📍 ${wh['address'] ?? 'Kurnool, AP'}',
                                              style: GoogleFonts.manrope(
                                                fontSize: 12,
                                                fontWeight: FontWeight.w600,
                                                color: const Color(0xFF64748B),
                                              ),
                                            ),
                                            const SizedBox(height: 4),
                                            Container(
                                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                                              decoration: BoxDecoration(
                                                color: tagBg,
                                                borderRadius: BorderRadius.circular(8),
                                              ),
                                              child: Text(
                                                '• $tagText',
                                                style: GoogleFonts.manrope(
                                                  fontSize: 10.5,
                                                  fontWeight: FontWeight.w800,
                                                  color: tagColor,
                                                ),
                                              ),
                                            ),
                                          ],
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 10),
                                Column(
                                  crossAxisAlignment: CrossAxisAlignment.end,
                                  children: [
                                    Text(
                                      'Available',
                                      style: GoogleFonts.manrope(
                                        fontSize: 10.5,
                                        fontWeight: FontWeight.w600,
                                        color: const Color(0xFF64748B),
                                      ),
                                    ),
                                    RichText(
                                      text: TextSpan(
                                        text: '$availQtl ',
                                        style: GoogleFonts.fraunces(
                                          fontSize: 20,
                                          fontWeight: FontWeight.w800,
                                          color: const Color(0xFF1E293B),
                                        ),
                                        children: [
                                          TextSpan(
                                            text: 'Qtl',
                                            style: GoogleFonts.manrope(
                                              fontSize: 12,
                                              fontWeight: FontWeight.w700,
                                              color: const Color(0xFF64748B),
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                    Text(
                                      'Total $totalQtl Qtl',
                                      style: GoogleFonts.manrope(
                                        fontSize: 10.5,
                                        fontWeight: FontWeight.w600,
                                        color: const Color(0xFF94A3B8),
                                      ),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                            const SizedBox(height: 12),

                            // Progress bar
                            ClipRRect(
                              borderRadius: BorderRadius.circular(3),
                              child: LinearProgressIndicator(
                                value: progressRatio,
                                minHeight: 6,
                                backgroundColor: const Color(0xFFF1F5F9),
                                valueColor: const AlwaysStoppedAnimation<Color>(AppColors.forest),
                              ),
                            ),
                            const SizedBox(height: 6),

                            Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                  'Used: $usedQtl Qtl',
                                  style: GoogleFonts.manrope(fontSize: 11, fontWeight: FontWeight.w600, color: const Color(0xFF64748B)),
                                ),
                                Text(
                                  'Available: $availQtl Qtl',
                                  style: GoogleFonts.manrope(fontSize: 11, fontWeight: FontWeight.w600, color: const Color(0xFF64748B)),
                                ),
                                Text(
                                  'Total: $totalQtl Qtl',
                                  style: GoogleFonts.manrope(fontSize: 11, fontWeight: FontWeight.w600, color: const Color(0xFF64748B)),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              const SizedBox(height: 24),

              // 4. Your Past Bookings Section
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    context.tr('yourPastBookings'),
                    style: GoogleFonts.fraunces(
                      fontSize: 16,
                      fontWeight: FontWeight.w800,
                      color: AppColors.forest,
                    ),
                  ),
                  if (_bookings.length > 3)
                    TextButton(
                      style: TextButton.styleFrom(
                        padding: EdgeInsets.zero,
                        minimumSize: Size.zero,
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                      onPressed: () => setState(() => _showAllBookings = !_showAllBookings),
                      child: Text(
                        _showAllBookings ? 'Show Less' : '${context.tr('viewAll')} >',
                        style: GoogleFonts.manrope(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w800,
                          color: const Color(0xFF28653F),
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 12),

              if (_bookings.isEmpty)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: const Color(0xFFCBD5E1), width: 1.5),
                  ),
                  child: Column(
                    children: [
                      const Text('🚚', style: TextStyle(fontSize: 32)),
                      const SizedBox(height: 6),
                      Text(
                        context.tr('noBookingsYet'),
                        style: GoogleFonts.fraunces(
                          fontSize: 15,
                          fontWeight: FontWeight.w800,
                          color: AppColors.forest,
                        ),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Book a warehouse drop-off slot for direct gate-in without waiting in queue.',
                        style: GoogleFonts.manrope(
                          fontSize: 12.5,
                          color: const Color(0xFF64748B),
                        ),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 14),
                      ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppColors.forest,
                          foregroundColor: Colors.white,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                        ),
                        onPressed: () => _openBookingModal(),
                        child: Text(
                          context.tr('bookFirstSlot'),
                          style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w800),
                        ),
                      ),
                    ],
                  ),
                )
              else
                ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: _showAllBookings ? _bookings.length : (_bookings.length > 3 ? 3 : _bookings.length),
                  separatorBuilder: (_, __) => const SizedBox(height: 12),
                  itemBuilder: (ctx, index) {
                    final slot = _bookings[index];
                    final dateInfo = formatDateParts(slot['booking_date'] as String?);
                    final whName = slot['warehouses']?['name'] as String? ?? 'Kurnool Warehouse Hub';

                    final statusStr = (slot['status'] as String? ?? 'confirmed').toLowerCase();
                    final isConfirmed = statusStr == 'confirmed' || statusStr == 'approved';
                    final isPending = statusStr == 'pending';

                    final statusDotColor = isConfirmed
                        ? const Color(0xFF166534)
                        : isPending
                            ? const Color(0xFFF59E0B)
                            : const Color(0xFF94A3B8);

                    final statusTextColor = isConfirmed
                        ? const Color(0xFF166534)
                        : isPending
                            ? const Color(0xFF92400E)
                            : const Color(0xFF475569);

                    final statusBgColor = isConfirmed
                        ? const Color(0xFFDCFCE7)
                        : isPending
                            ? const Color(0xFFFEF3C7)
                            : const Color(0xFFF1F5F9);

                    return InkWell(
                      borderRadius: BorderRadius.circular(18),
                      onTap: () => _showBookingDetailsModal(slot),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(18),
                          border: Border.all(color: const Color(0xFFE2E8F0)),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x05000000),
                              blurRadius: 14,
                              offset: Offset(0, 4),
                            ),
                          ],
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                // Grain type thumbnail image
                                ClipRRect(
                                  borderRadius: BorderRadius.circular(10),
                                  child: Image.asset(
                                    getGrainAsset(slot['grain_type'] as String?),
                                    width: 52,
                                    height: 52,
                                    fit: BoxFit.cover,
                                    errorBuilder: (_, __, ___) => Container(
                                      width: 52,
                                      height: 52,
                                      decoration: const BoxDecoration(
                                        color: Color(0xFFE7F4EB),
                                        borderRadius: BorderRadius.all(Radius.circular(10)),
                                      ),
                                      alignment: Alignment.center,
                                      child: Text(
                                        getGrainEmoji(slot['grain_type'] as String?),
                                        style: const TextStyle(fontSize: 24),
                                      ),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        whName,
                                        style: GoogleFonts.fraunces(
                                          fontSize: 14.5,
                                          fontWeight: FontWeight.w800,
                                          color: const Color(0xFF1E293B),
                                        ),
                                        maxLines: 2,
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                      const SizedBox(height: 3),
                                      Row(
                                        children: [
                                          Container(
                                            width: 6,
                                            height: 6,
                                            decoration: BoxDecoration(
                                              color: statusDotColor,
                                              shape: BoxShape.circle,
                                            ),
                                          ),
                                          const SizedBox(width: 5),
                                          Text(
                                            '${dateInfo.day} ${dateInfo.monthYear}',
                                            style: GoogleFonts.manrope(
                                              fontSize: 11.5,
                                              fontWeight: FontWeight.w700,
                                              color: const Color(0xFF64748B),
                                            ),
                                          ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                                  decoration: BoxDecoration(
                                    color: statusBgColor,
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                  child: Text(
                                    (slot['status'] as String? ?? 'Confirmed').toUpperCase(),
                                    style: GoogleFonts.manrope(
                                      fontSize: 11,
                                      fontWeight: FontWeight.w800,
                                      color: statusTextColor,
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 4),
                                const Icon(Icons.chevron_right, size: 18, color: Color(0xFF94A3B8)),
                              ],
                            ),
                            const SizedBox(height: 10),
                            Container(height: 1, color: const Color(0xFFF1F5F9)),
                            const SizedBox(height: 10),
                            Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                  '${getGrainEmoji(slot['grain_type'])} ${slot['grain_type'] ?? 'Grain'}',
                                  style: GoogleFonts.manrope(
                                    fontSize: 12.5,
                                    fontWeight: FontWeight.w700,
                                    color: const Color(0xFF1E293B),
                                  ),
                                ),
                                Row(
                                  children: [
                                    Text(
                                      formatQtl(slot['quantity_kg']),
                                      style: GoogleFonts.manrope(
                                        fontSize: 12,
                                        fontWeight: FontWeight.w800,
                                        color: const Color(0xFF166534),
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    Text(
                                      '•  🕐 10:30 AM',
                                      style: GoogleFonts.manrope(
                                        fontSize: 11,
                                        fontWeight: FontWeight.w600,
                                        color: const Color(0xFF64748B),
                                      ),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              const SizedBox(height: 24),

              // 5. Today's APMC Mandi Market Rates
              if (_marketRates.isNotEmpty) ...[
                const Divider(color: Color(0xFFE2E8F0)),
                const SizedBox(height: 14),
                Text(
                  context.tr('todaysMandiKurnool'),
                  style: GoogleFonts.manrope(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w800,
                    color: const Color(0xFF64748B),
                    letterSpacing: 0.5,
                  ),
                ),
                const SizedBox(height: 10),
                GridView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: 2,
                    mainAxisSpacing: 10,
                    crossAxisSpacing: 10,
                    childAspectRatio: 1.75,
                  ),
                  itemCount: _marketRates.length > 4 ? 4 : _marketRates.length,
                  itemBuilder: (ctx, idx) {
                    final rate = _marketRates[idx];
                    final pricePerKg = (rate['price_per_kg'] as num?)?.toDouble() ?? 0.0;
                    final pricePerQtl = (pricePerKg * 100).round();

                    final cropType = rate['crop_type'] as String? ?? '';
                    return Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(color: const Color(0xFFE2E8F0)),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Row(
                            children: [
                              // Crop image thumbnail
                              ClipRRect(
                                borderRadius: BorderRadius.circular(7),
                                child: Image.asset(
                                  getGrainAsset(cropType),
                                  width: 30,
                                  height: 30,
                                  fit: BoxFit.cover,
                                  errorBuilder: (_, __, ___) => Text(
                                    getGrainEmoji(cropType),
                                    style: const TextStyle(fontSize: 20),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 7),
                              Expanded(
                                child: Text(
                                  cropType.isEmpty ? 'Crop' : cropType,
                                  style: GoogleFonts.manrope(
                                    fontSize: 12.5,
                                    fontWeight: FontWeight.w800,
                                    color: const Color(0xFF1E293B),
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                                decoration: BoxDecoration(
                                  color: const Color(0xFFE7F4EB),
                                  borderRadius: BorderRadius.circular(6),
                                ),
                                child: Text(
                                  'Grade ${rate['grade'] ?? 'A'}',
                                  style: GoogleFonts.manrope(
                                    fontSize: 9.5,
                                    fontWeight: FontWeight.w800,
                                    color: const Color(0xFF166534),
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 5),
                          RichText(
                            text: TextSpan(
                              text: '₹${NumberFormat('#,##,###').format(pricePerQtl)} ',
                              style: GoogleFonts.fraunces(
                                fontSize: 16,
                                fontWeight: FontWeight.w800,
                                color: AppColors.forest,
                              ),
                              children: [
                                TextSpan(
                                  text: '/ Qtl',
                                  style: GoogleFonts.manrope(
                                    fontSize: 10,
                                    fontWeight: FontWeight.w600,
                                    color: const Color(0xFF64748B),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            '₹$pricePerKg / Kg',
                            style: GoogleFonts.manrope(
                              fontSize: 10.5,
                              fontWeight: FontWeight.w700,
                              color: const Color(0xFF16A34A),
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  void _showWarehouseFilterSheet() {
    final districts = <String>{'All'};
    for (final w in _warehouses) {
      final d = w['district'] as String? ?? w['address']?.toString().split(',').last.trim();
      if (d != null && d.isNotEmpty) districts.add(d);
    }

    String tempDistrict = _filterDistrict ?? 'All';
    double tempMinCap = _minCapacityKg ?? 0.0;
    bool tempAvail = _filterAvailableOnly;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (sheetCtx) {
        return StatefulBuilder(
          builder: (modalCtx, setModalState) {
            return Padding(
              padding: EdgeInsets.only(
                bottom: MediaQuery.of(sheetCtx).viewInsets.bottom,
                left: 20,
                right: 20,
                top: 14,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Center(
                    child: Container(
                      width: 38,
                      height: 4,
                      decoration: BoxDecoration(
                        color: const Color(0xFFE2E8F0),
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        'Filter Warehouses',
                        style: GoogleFonts.fraunces(
                          fontSize: 18,
                          fontWeight: FontWeight.w800,
                          color: AppColors.forest,
                        ),
                      ),
                      TextButton(
                        onPressed: () {
                          setModalState(() {
                            tempDistrict = 'All';
                            tempMinCap = 0.0;
                            tempAvail = false;
                          });
                        },
                        child: const Text('Reset', style: TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF2E7D5B))),
                      ),
                    ],
                  ),
                  const Divider(height: 1, color: Color(0xFFE2E8F0)),
                  const SizedBox(height: 14),
                  const Text('District / Region', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Color(0xFF1E293B))),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: districts.map((d) {
                      final isSelected = tempDistrict.toLowerCase() == d.toLowerCase();
                      return ChoiceChip(
                        label: Text(d, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: isSelected ? Colors.white : const Color(0xFF334155))),
                        selected: isSelected,
                        selectedColor: AppColors.forest,
                        backgroundColor: const Color(0xFFF1F5F9),
                        onSelected: (val) {
                          setModalState(() => tempDistrict = d);
                        },
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 16),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Available Capacity Only', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Color(0xFF1E293B))),
                    subtitle: const Text('Hide warehouses that are at 100% capacity', style: TextStyle(fontSize: 11.5, color: Color(0xFF64748B))),
                    value: tempAvail,
                    activeColor: AppColors.canopy,
                    onChanged: (v) => setModalState(() => tempAvail = v),
                  ),
                  const SizedBox(height: 18),
                  SizedBox(
                    width: double.infinity,
                    height: 48,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.forest,
                        foregroundColor: Colors.white,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                      onPressed: () {
                        setState(() {
                          _filterDistrict = tempDistrict;
                          _minCapacityKg = tempMinCap;
                          _filterAvailableOnly = tempAvail;
                        });
                        Navigator.pop(sheetCtx);
                      },
                      child: const Text('Apply Filter', style: TextStyle(fontWeight: FontWeight.w800)),
                    ),
                  ),
                  const SizedBox(height: 20),
                ],
              ),
            );
          },
        );
      },
    );
  }

}
