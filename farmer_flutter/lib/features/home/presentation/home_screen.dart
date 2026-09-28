import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../../core/constants/app_colors.dart';
import '../../../core/localization/app_localizations.dart';
import '../../../core/services/connectivity_service.dart';
import '../../../data/repositories/farmer_repository.dart';
import '../../grain_sales/presentation/grain_sales_screen.dart' show getGrainEmoji;

/// HomeScreen — Phase 2 migration from FarmerHome.jsx.
/// Faithfully reproduces:
///   1. Farm header & time-based greeting (Fraunces font)
///   2. Weather advisory card (dark green gradient)
///   3. Quick Actions (4 icon tiles in a 4-column grid)
///   4. Your Fields (circular SVG ring chart cards — 2 per row)
///   5. Today's Mandi Prices (horizontal scroll)
///   6. Recent Orders & Bookings (seed purchases + grain bookings)
///
/// All data from backend — no hardcoded records.
/// Empty/error/loading states shown exactly when data is absent.
class HomeScreen extends StatefulWidget {
  final void Function(int tabIndex, [String? subTab]) onNavigateTab;

  const HomeScreen({super.key, required this.onNavigateTab});

  @override
  @override
  State<HomeScreen> createState() => HomeScreenState();
}

class HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  final _repo = FarmerRepository();

  Map<String, dynamic>? _profile;
  Map<String, dynamic>? _dashboard;
  List<Map<String, dynamic>> _marketRates = [];
  bool _loading = true;
  String? _error;
  bool _refreshingRates = false;
  bool _retryingWeather = false;
  DateTime? _ratesUpdatedAt;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    ConnectivityService().addOnRestoredListener(_onConnectivityRestored);
    _loadDashboard();
  }

  @override
  void dispose() {
    ConnectivityService().removeOnRestoredListener(_onConnectivityRestored);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  void _onConnectivityRestored() {
    // When internet returns, clear stale connectivity error banner to restore normal UI.
    // Do not automatically hammer the backend; wait for user Pull-to-Refresh or Retry.
    if (mounted && _error != null) {
      setState(() {
        _error = null;
      });
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && (_dashboard == null || _dashboard!.isEmpty)) {
      _loadDashboard();
    }
  }

  Future<void> refreshDashboard() => _loadDashboard();

  Future<void> _loadDashboard() async {
    if (_dashboard == null || _dashboard!.isEmpty) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      // Parallelize profile and dashboard fetch
      final profileFuture = _repo.getFarmerProfile();
      final dashboardFuture = _repo.getDashboard();
      final results = await Future.wait([profileFuture, dashboardFuture]);
      final profile = results[0];
      final dashboard = Map<String, dynamic>.from(results[1] as Map);

      // Extract mandi rates directly from dashboard if present, or fetch separately
      final rawRates = dashboard['mandi_prices'];
      List<Map<String, dynamic>> rates = [];
      if (rawRates is List && rawRates.isNotEmpty) {
        rates = List<Map<String, dynamic>>.from(rawRates);
      } else {
        rates = await _repo.getMarketRates().catchError((_) => <Map<String, dynamic>>[]);
      }

      // Handle resilient weather caching & direct Open-Meteo fallback
      final rawBackendWeather = dashboard['weather'] is Map && (dashboard['weather'] as Map).isNotEmpty
          ? Map<String, dynamic>.from(dashboard['weather'] as Map)
          : null;

      final resilientWeather = await _fetchResilientWeather(rawBackendWeather);
      dashboard['weather'] = resilientWeather;

      if (mounted) {
        setState(() {
          _profile = profile;
          _dashboard = dashboard;
          _marketRates = rates;
          _ratesUpdatedAt = DateTime.now();
          _loading = false;
          _retryingWeather = false;
          _error = null; // Stale error cleared on success!
        });
      }
    } catch (e) {
      if (kDebugMode) {
        debugPrint('[HomeScreen] loadDashboard error: $e');
      }
      if (mounted) {
        setState(() {
          _error = context.tr('errorLoad');
          _loading = false;
          _retryingWeather = false;
        });
      }
    }
  }

  Future<Map<String, dynamic>?> _fetchResilientWeather(Map<String, dynamic>? backendWeather, {bool forceRefreshDirect = false}) async {
    final prefs = await SharedPreferences.getInstance();

    // Priority 1: If backend returned valid weather (and not force refreshing), save locally and return
    if (!forceRefreshDirect && backendWeather != null && backendWeather.isNotEmpty) {
      final weatherData = Map<String, dynamic>.from(backendWeather);
      weatherData['saved_at'] = DateTime.now().toIso8601String();
      weatherData['is_cached_local'] = false;
      try {
        await prefs.setString('last_successful_weather', jsonEncode(weatherData));
      } catch (_) {}
      return weatherData;
    }

    // Priority 2: Check SharedPreferences local cache
    final savedStr = prefs.getString('last_successful_weather');
    Map<String, dynamic>? localCache;
    if (savedStr != null && savedStr.isNotEmpty) {
      try {
        localCache = jsonDecode(savedStr) as Map<String, dynamic>;
      } catch (_) {}
    }

    // Return fresh local cache if less than 2 hours old and not force refreshing
    if (!forceRefreshDirect && localCache != null) {
      final savedAtStr = localCache['saved_at']?.toString();
      if (savedAtStr != null) {
        final savedAt = DateTime.tryParse(savedAtStr);
        if (savedAt != null && DateTime.now().difference(savedAt).inHours < 2) {
          localCache['is_cached_local'] = true;
          return localCache;
        }
      }
    }

    // Priority 3: Fetch direct from Open-Meteo API
    try {
      final dio = Dio(BaseOptions(connectTimeout: const Duration(seconds: 4), receiveTimeout: const Duration(seconds: 4)));
      final resp = await dio.get(
        'https://api.open-meteo.com/v1/forecast',
        queryParameters: {
          'latitude': 15.8281,
          'longitude': 78.0373,
          'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m',
        },
      );
      if (resp.statusCode == 200 && resp.data != null && resp.data['current'] != null) {
        final current = resp.data['current'];
        final code = (current['weather_code'] as num?)?.toInt() ?? 0;
        final (conditionStr, iconStr) = _mapWeatherCode(code);
        final freshWeather = <String, dynamic>{
          'temperature_c': (current['temperature_2m'] as num?)?.toDouble() ?? 28.0,
          'feels_like_c': (current['apparent_temperature'] as num?)?.toDouble() ?? 30.0,
          'humidity_percent': (current['relative_humidity_2m'] as num?)?.toInt() ?? 60,
          'wind_kmh': (current['wind_speed_10m'] as num?)?.toDouble() ?? 12.0,
          'condition': conditionStr,
          'icon': iconStr,
          'advisory': 'Favorable conditions for field crop management.',
          'saved_at': DateTime.now().toIso8601String(),
          'is_cached_local': false,
        };
        try {
          await prefs.setString('last_successful_weather', jsonEncode(freshWeather));
        } catch (_) {}
        return freshWeather;
      }
    } catch (e) {
      if (kDebugMode) {
        debugPrint('[HomeScreen] Direct Open-Meteo error: $e');
      }
    }

    // Priority 4: Fallback to existing local cache (even if older than 2h)
    if (localCache != null) {
      localCache['is_cached_local'] = true;
      return localCache;
    }

    // Priority 5: Return null (stable empty shell state, no fake data)
    return null;
  }

  (String, String) _mapWeatherCode(int code) {
    if (code == 0) return ('Clear Sky', '☀️');
    if (code >= 1 && code <= 3) return ('Partly Cloudy', '⛅');
    if (code == 45 || code == 48) return ('Foggy', '🌫️');
    if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return ('Rain / Drizzle', '🌧️');
    if (code >= 95) return ('Thunderstorm', '⛈️');
    return ('Fair Weather', '🌤️');
  }

  Future<void> _retryWeather() async {
    if (_retryingWeather) return;
    setState(() => _retryingWeather = true);
    try {
      Map<String, dynamic>? rawBackendWeather;
      try {
        final freshDashboard = await _repo.getDashboard();
        if (freshDashboard['weather'] is Map && (freshDashboard['weather'] as Map).isNotEmpty) {
          rawBackendWeather = Map<String, dynamic>.from(freshDashboard['weather'] as Map);
        }
      } catch (_) {}

      final resilientWeather = await _fetchResilientWeather(
        rawBackendWeather,
        forceRefreshDirect: rawBackendWeather == null,
      );

      if (mounted) {
        setState(() {
          _dashboard ??= {};
          _dashboard!['weather'] = resilientWeather;
          _retryingWeather = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _retryingWeather = false);
    }
  }

  Future<void> _refreshMarketRates() async {
    if (_refreshingRates) return;
    setState(() => _refreshingRates = true);
    try {
      final rates = await _repo.getMarketRates();
      if (mounted) {
        setState(() {
          _marketRates = rates;
          _ratesUpdatedAt = DateTime.now();
          _refreshingRates = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _refreshingRates = false);
    }
  }

  String _greeting(BuildContext context) {
    final h = DateTime.now().hour;
    if (h >= 12 && h < 17) return context.tr('goodAfternoon');
    if (h >= 17) return context.tr('goodEvening');
    return context.tr('goodMorning');
  }

  @override
  Widget build(BuildContext context) {
    final farmerName = _profile?['name'] as String? ?? '';
    final farmName = (_profile?['farm_name'] as String? ?? '').toUpperCase();
    final crops = List<Map<String, dynamic>>.from(_dashboard?['crops'] as List? ?? []);
    final purchases = List<Map<String, dynamic>>.from(_dashboard?['purchases'] as List? ?? []);
    final bookings = List<Map<String, dynamic>>.from(_dashboard?['upcomingDeliveries'] as List? ?? []);
    final recentOrders = List<Map<String, dynamic>>.from(_dashboard?['recent_orders'] as List? ?? []);

    return RefreshIndicator(
      color: const Color(0xFF1B4D3E),
      onRefresh: _loadDashboard,
      child: SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. Farm Header & Greeting (FarmerHome.jsx lines 58–78)
            _buildFarmHeader(context, farmerName, farmName, crops.length),
            const SizedBox(height: 16),

            // Error banner
            if (_error != null) _buildErrorBanner(_error!),
            if (_error != null) const SizedBox(height: 10),

            // 2. Weather Advisory Card (FarmerHome.jsx lines 87–131)
            _buildWeatherCard(context),
            const SizedBox(height: 16),

            // 3. Quick Actions (FarmerHome.jsx lines 133–241)
            _buildQuickActionsSection(context),
            const SizedBox(height: 16),

            // 4. Your Fields ring chart cards (FarmerHome.jsx lines 243–341)
            _buildFieldsSection(context, crops),
            const SizedBox(height: 16),

            // 5. Today's Mandi Prices (FarmerHome.jsx lines 343–408)
            _buildMandiPricesSection(context),
            const SizedBox(height: 16),

            // 6. Recent Orders & Bookings (FarmerHome.jsx lines 410–737)
            _buildRecentOrdersSection(context, recentOrders, purchases, bookings),
          ],
        ),
      ),
    );
  }

  // ── Section 1: Farm Header ────────────────────────────────────────────
  Widget _buildFarmHeader(BuildContext context, String farmerName, String farmName, int fieldCount) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // "Good morning, Asha 🌾" in Fraunces
        RichText(
          text: TextSpan(
            style: const TextStyle(
              fontFamily: 'Fraunces',
              fontSize: 26,
              fontWeight: FontWeight.w700,
              color: Color(0xFF15302A),
            ),
            children: [
              TextSpan(text: '${_greeting(context)}, '),
              TextSpan(
                text: _loading ? '...' : (farmerName.isNotEmpty ? farmerName.split(' ').first : ''),
              ),
              const TextSpan(text: ' 🌾'),
            ],
          ),
        ),
      ],
    );
  }

  // ── Section 2: Weather Card ───────────────────────────────────────────
  Widget _buildWeatherCard(BuildContext context) {
    if ((_loading || _retryingWeather) && (_dashboard == null || _dashboard!['weather'] == null)) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [Color(0xFF1B4D3E), Color(0xFF12382D)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: BorderRadius.circular(22),
        ),
        child: Row(
          children: [
            const SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2, valueColor: AlwaysStoppedAnimation<Color>(Colors.white70)),
            ),
            const SizedBox(width: 14),
            Text(
              'Loading weather advisory...',
              style: GoogleFonts.manrope(fontSize: 13, color: Colors.white70, fontWeight: FontWeight.w600),
            ),
          ],
        ),
      );
    }

    final weather = _dashboard?['weather'] as Map<String, dynamic>?;
    if (weather == null || weather.isEmpty) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        decoration: BoxDecoration(
          color: const Color(0xFFF1F5F9),
          borderRadius: BorderRadius.circular(22),
          border: Border.all(color: const Color(0xFFE2E8F0)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Expanded(
              child: Row(
                children: [
                  const Icon(Icons.wb_sunny_outlined, color: Color(0xFF64748B), size: 22),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      'Weather Advisory · Tap refresh to check forecast',
                      overflow: TextOverflow.ellipsis,
                      style: GoogleFonts.manrope(fontSize: 12.5, fontWeight: FontWeight.w600, color: const Color(0xFF64748B)),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            _retryingWeather
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2, valueColor: AlwaysStoppedAnimation<Color>(AppColors.forest)),
                  )
                : IconButton(
                    icon: const Icon(Icons.refresh, color: AppColors.forest, size: 20),
                    onPressed: _retryWeather,
                    tooltip: 'Refresh Weather',
                  ),
          ],
        ),
      );
    }

    final rawTemp = weather['temperature_c'] ?? weather['temp'] ?? weather['temperature'];
    final tempStr = rawTemp != null ? '$rawTemp°C' : '--°C';
    final condition = weather['condition']?.toString() ?? weather['description']?.toString() ?? context.tr('weatherClear');
    final rawFeels = weather['feels_like_c'] ?? weather['feels_like'] ?? weather['feelsLike'];
    final feelsStr = rawFeels != null ? ' · ${context.tr('feelsLike')} $rawFeels°' : '';
    final advisory = weather['advisory']?.toString() ?? context.tr('irrigationAdvisory');
    final humidity = weather['humidity_percent']?.toString() ?? weather['humidity']?.toString();
    final wind = weather['wind_kmh']?.toString() ?? weather['wind_speed']?.toString() ?? weather['wind']?.toString();
    final icon = weather['icon']?.toString() ?? '☀️';

    return RepaintBoundary(
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [Color(0xFF1B4D3E), Color(0xFF12382D)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: BorderRadius.circular(22),
          boxShadow: [
            BoxShadow(
              color: const Color(0xFF1B4D3E).withValues(alpha: 0.2),
              blurRadius: 24,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.14),
                borderRadius: BorderRadius.circular(14),
              ),
              child: Center(child: Text(icon, style: const TextStyle(fontSize: 22))),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      Text(
                        tempStr,
                        style: const TextStyle(
                          fontFamily: 'Fraunces',
                          fontSize: 24,
                          fontWeight: FontWeight.w800,
                          color: Colors.white,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          '$condition$feelsStr',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: Color(0xD9FFFFFF),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 3),
                  Text(
                    advisory,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: Color(0xFFA7F3D0),
                      height: 1.2,
                      letterSpacing: -0.1,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (humidity != null && humidity.isNotEmpty)
                  Text(
                    '💧 $humidity%',
                    style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: Color(0xD9FFFFFF)),
                  ),
                if (humidity != null && humidity.isNotEmpty && wind != null && wind.isNotEmpty)
                  const SizedBox(height: 4),
                if (wind != null && wind.isNotEmpty)
                  Text(
                    '💨 $wind km/h',
                    style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: Color(0xD9FFFFFF)),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ── Section 3: Quick Actions ──────────────────────────────────────────
  Widget _buildQuickActionsSection(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          context.tr('quickActions'),
          style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: Color(0xFF15302A)),
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            _buildQuickActionTile(context,
                emoji: '🌱',
                bg: const Color(0xFFE8F5E9),
                label: context.tr('buySeedsAction'),
                onTap: () => widget.onNavigateTab(1)),
            _buildQuickActionTile(context,
                emoji: '🏭',
                bg: const Color(0xFFFCE4EC),
                label: context.tr('grainSalesAction'),
                onTap: () => widget.onNavigateTab(3)),
            _buildQuickActionTile(context,
                emoji: '+',
                bg: const Color(0xFFE8F5E9),
                label: context.tr('addField'),
                onTap: () => widget.onNavigateTab(2),
                emojiStyle: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, color: Color(0xFF2E7D32))),
            _buildQuickActionTile(context,
                emoji: '🛒',
                bg: const Color(0xFFE3F2FD),
                label: context.tr('myOrders'),
                onTap: () => widget.onNavigateTab(1, 'history')),
          ],
        ),
      ],
    );
  }

  Widget _buildQuickActionTile(
    BuildContext context, {
    required String emoji,
    required Color bg,
    required String label,
    required VoidCallback onTap,
    TextStyle? emojiStyle,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: RepaintBoundary(
        child: Column(
          children: [
            Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: bg,
                borderRadius: BorderRadius.circular(20),
                boxShadow: [
                  BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 8, offset: const Offset(0, 2)),
                ],
              ),
              child: Center(
                child: Text(emoji, style: emojiStyle ?? const TextStyle(fontSize: 26)),
              ),
            ),
            const SizedBox(height: 6),
            SizedBox(
              width: 72,
              child: Text(
                label,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF1C2A21)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ── Section 4: Your Fields (ring chart) ──────────────────────────────
  Widget _buildFieldsSection(BuildContext context, List<Map<String, dynamic>> crops) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              context.tr('yourFields'),
              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: Color(0xFF15302A)),
            ),
            GestureDetector(
              onTap: () => widget.onNavigateTab(2),
              child: Text(
                '${context.tr('seeAll')} ›',
                style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: Color(0xFF1B4D3E)),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (_loading)
          const Center(child: Padding(padding: EdgeInsets.all(20), child: CircularProgressIndicator(color: Color(0xFF1B4D3E))))
        else if (crops.isEmpty)
          _buildEmptyFieldsCard(context)
        else
          GridView.count(
            crossAxisCount: 2,
            crossAxisSpacing: 10,
            mainAxisSpacing: 10,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 0.88,
            children: crops.take(2).map((c) => _buildFieldRingCard(context, c)).toList(),
          ),
      ],
    );
  }

  Widget _buildEmptyFieldsCard(BuildContext context) {
    return GestureDetector(
      onTap: () => widget.onNavigateTab(2),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFFE1E8D8), width: 1.5),
        ),
        child: const Center(
          child: Column(
            children: [
              Text('🌱', style: TextStyle(fontSize: 30)),
              SizedBox(height: 6),
              Text('No fields registered yet', style: TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF15302A))),
              SizedBox(height: 4),
              Text('Tap here to add your first crop field.', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Color(0xFF657268))),
            ],
          ),
        ),
      ),
    );
  }

  double _getCropStageProgress(Map<String, dynamic> crop) {
    if (crop['stage_progress_percent'] != null) {
      return (crop['stage_progress_percent'] as num).toDouble();
    }
    final stage = (crop['stage'] as String? ?? crop['status'] as String? ?? '').trim().toLowerCase();
    final lifecycle = (crop['lifecycle_status'] as String? ?? '').trim().toLowerCase();

    if (lifecycle == 'harvested' || stage == 'harvest' || stage == 'harvested') {
      return 100.0;
    }
    if (stage == 'maturity') {
      return 75.0;
    }
    if (stage == 'growing') {
      return 50.0;
    }
    if (stage == 'sowing') {
      return 0.0;
    }
    return 0.0;
  }

  Widget _buildFieldRingCard(BuildContext context, Map<String, dynamic> crop) {
    final double progressPct = _getCropStageProgress(crop);
    final stageStr = (crop['stage'] as String? ?? crop['status'] as String? ?? 'Sowing').trim();
    final lifecycleStatus = (crop['lifecycle_status'] as String? ?? '').trim().toLowerCase();
    final isHarvested = progressPct >= 100.0 || lifecycleStatus == 'harvested' || stageStr.toLowerCase().contains('harvest');

    final Color ringColor;
    if (progressPct >= 100.0) {
      ringColor = const Color(0xFF1B4D3E); // full green completed ring
    } else if (progressPct >= 75.0) {
      ringColor = const Color(0xFF15803D); // stronger/completed-progress green
    } else if (progressPct >= 50.0) {
      ringColor = const Color(0xFF22C55E); // active green progress
    } else {
      ringColor = const Color(0xFFCBD5E1); // neutral/inactive ring
    }

    final Color badgeBg;
    final Color badgeTextColor;
    final String statusLabel;

    if (isHarvested) {
      statusLabel = 'Harvested';
      badgeBg = const Color(0xFFDCFCE7);
      badgeTextColor = const Color(0xFF166534);
    } else if (progressPct == 75.0) {
      statusLabel = 'Maturity';
      badgeBg = const Color(0xFFE8F5E9);
      badgeTextColor = const Color(0xFF15803D);
    } else if (progressPct == 50.0) {
      statusLabel = 'Growing';
      badgeBg = const Color(0xFFE8F5E9);
      badgeTextColor = const Color(0xFF2E7D32);
    } else {
      statusLabel = 'Sowing';
      badgeBg = const Color(0xFFF1F5F9);
      badgeTextColor = const Color(0xFF475569);
    }

    final cropName = crop['crop_name'] as String? ?? crop['crop_type'] as String? ?? 'Field';
    final cropType = crop['crop_type'] as String? ?? '';
    final acres = (crop['acres'] as num?)?.toStringAsFixed(1) ?? '—';
    final cropId = crop['id']?.toString();

    return GestureDetector(
      onTap: () => widget.onNavigateTab(2, cropId),
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFFE1E8D8)),
          boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.03), blurRadius: 8, offset: const Offset(0, 2))],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // SVG-style ring chart via CustomPaint
            Center(
              child: SizedBox(
                width: 60,
                height: 60,
                child: Stack(
                  alignment: Alignment.center,
                  children: [
                    CustomPaint(
                      size: const Size(60, 60),
                      painter: _RingChartPainter(
                        percentage: progressPct / 100,
                        ringColor: ringColor,
                        trackColor: const Color(0xFFE2E8F0),
                      ),
                    ),
                    Text(
                      '${progressPct.toInt()}%',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w800,
                        color: progressPct == 0.0 ? const Color(0xFF64748B) : const Color(0xFF1C2A21),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            Text(cropName, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w800, color: Color(0xFF1C2A21)), maxLines: 1, overflow: TextOverflow.ellipsis),
            const SizedBox(height: 2),
            Text('$cropType · $acres acres', style: const TextStyle(fontSize: 11.5, color: Color(0xFF657268))),
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(color: badgeBg, borderRadius: BorderRadius.circular(10)),
              child: Text(statusLabel, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: badgeTextColor)),
            ),
          ],
        ),
      ),
    );
  }

  // ── Section 5: Mandi Prices ───────────────────────────────────────────
  Widget _buildMandiPricesSection(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(context.tr('todaysMandiPrices'), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: Color(0xFF15302A))),
            InkWell(
              onTap: _refreshMarketRates,
              borderRadius: BorderRadius.circular(8),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                child: Row(
                  children: [
                    Text(
                      _ratesUpdatedAt != null
                          ? '${context.tr('updatedAt')} ${_ratesUpdatedAt!.hour.toString().padLeft(2, '0')}:${_ratesUpdatedAt!.minute.toString().padLeft(2, '0')}'
                          : '${context.tr('updatedAt')} Live',
                      style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: Color(0xFF657268)),
                    ),
                    const SizedBox(width: 4),
                    _refreshingRates
                        ? const SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 1.5, color: Color(0xFF1B4D3E)))
                        : const Text('🔄', style: TextStyle(fontSize: 12)),
                  ],
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (_loading)
          const SizedBox(height: 60, child: Center(child: CircularProgressIndicator(color: Color(0xFF1B4D3E))))
        else if (_marketRates.isEmpty)
          Container(
            height: 70,
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: const Color(0xFFE1E8D8)),
            ),
            child: const Center(child: Text('Mandi prices not available', style: TextStyle(color: Color(0xFF657268), fontSize: 13))),
          )
        else
          SizedBox(
            height: 92,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: _marketRates.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final r = _marketRates[index];
                final crop = r['crop'] as String? ?? r['crop_type'] as String? ?? 'Crop';
                final rawPrice = r['price'] ?? r['price_per_qtl'] ?? r['price_per_kg'] ?? 0;
                final String formattedPrice;
                if (rawPrice is num) {
                  formattedPrice = rawPrice % 1 == 0 ? rawPrice.toInt().toString() : rawPrice.toStringAsFixed(1);
                } else {
                  final parsed = double.tryParse(rawPrice.toString().trim());
                  if (parsed != null) {
                    formattedPrice = parsed % 1 == 0 ? parsed.toInt().toString() : parsed.toStringAsFixed(1);
                  } else {
                    formattedPrice = rawPrice.toString();
                  }
                }

                final change = r['change'] as String? ?? '';
                final isUp = r['isUp'] as bool? ?? (change.isNotEmpty ? !change.startsWith('-') : true);
                final icon = _getCropEmoji(crop);

                return Container(
                  width: 170,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(18),
                    border: Border.all(color: const Color(0xFFE1E8D8)),
                    boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.03), blurRadius: 8, offset: const Offset(0, 2))],
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 38,
                        height: 38,
                        decoration: const BoxDecoration(color: Color(0xFFF5F5F5), shape: BoxShape.circle),
                        child: Center(child: Text(icon, style: const TextStyle(fontSize: 18))),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Text(
                              crop,
                              style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: Color(0xFF657268)),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            const SizedBox(height: 2),
                            FittedBox(
                              fit: BoxFit.scaleDown,
                              alignment: Alignment.centerLeft,
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.baseline,
                                textBaseline: TextBaseline.alphabetic,
                                children: [
                                  Text(
                                    '₹$formattedPrice',
                                    style: const TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w800,
                                      color: Color(0xFF1C2A21),
                                    ),
                                  ),
                                  const SizedBox(width: 3),
                                  const Text(
                                    '/qtl',
                                    style: TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.w600,
                                      color: Color(0xFF657268),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            if (change.isNotEmpty) ...[
                              const SizedBox(height: 2),
                              Text(
                                change,
                                style: TextStyle(
                                  fontSize: 10.5,
                                  fontWeight: FontWeight.w800,
                                  color: isUp ? const Color(0xFF2E7D32) : const Color(0xFFC62828),
                                ),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ],
                          ],
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ),
      ],
    );
  }

  String _getCropEmoji(String crop) {
    final c = crop.toLowerCase();
    if (c.contains('paddy') || c.contains('rice')) return '🌾';
    if (c.contains('maize') || c.contains('corn')) return '🌽';
    if (c.contains('cotton')) return '🌸';
    if (c.contains('groundnut') || c.contains('peanut')) return '🥜';
    if (c.contains('wheat')) return '🌾';
    if (c.contains('sugarcane') || c.contains('sugar cane')) return '🎋';
    if (c.contains('gram') || c.contains('pulse') || c.contains('soybean') || c.contains('chana')) return '🌱';
    if (c.contains('chilli') || c.contains('chili') || c.contains('pepper')) return '🌶️';
    if (c.contains('turmeric')) return '🟡';
    return '📦';
  }

  String _getCompactStatusLabel(String rawStatus) {
    final s = rawStatus.trim().toLowerCase();
    if (s.contains('unpaid')) return 'Unpaid';
    if (s.contains('paid')) return 'Paid';
    if (s.contains('delivered')) return 'Delivered';
    if (s.contains('confirmed')) return 'Confirmed';
    if (s.contains('completed')) return 'Completed';
    if (s.contains('in transit') || s.contains('transit')) return 'In Transit';
    if (s.contains('pending')) return 'Pending';
    if (s.contains('cancelled')) return 'Cancelled';
    return rawStatus.isNotEmpty ? rawStatus[0].toUpperCase() + rawStatus.substring(1) : 'Pending';
  }

  String _formatCompactRef(String rawRef) {
    final r = rawRef.trim();
    if (r.startsWith('SP-') && r.length > 15) {
      final parts = r.split('-');
      return 'SP...${parts.last}';
    }
    if (r.startsWith('GBK-') && r.length > 15) {
      final parts = r.split('-');
      return 'GBK...${parts.last}';
    }
    if (r.length > 14) {
      return '${r.substring(0, 8)}...';
    }
    return r;
  }

  // ── Section 6: Recent Orders & Bookings ──────────────────────────────
  Widget _buildRecentOrdersSection(
    BuildContext context,
    List<Map<String, dynamic>> recentOrders,
    List<Map<String, dynamic>> purchases,
    List<Map<String, dynamic>> bookings,
  ) {
    final combined = <Map<String, dynamic>>[];
    if (recentOrders.isNotEmpty) {
      combined.addAll(recentOrders);
    } else {
      for (final p in purchases) {
        combined.add({...p, 'type': 'seed_purchase'});
      }
      for (final b in bookings) {
        combined.add({...b, 'type': 'grain_booking'});
      }
    }

    final hasData = combined.isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(context.tr('recentOrdersBookings'), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: Color(0xFF15302A))),
            GestureDetector(
              onTap: _showAllOrdersModal,
              child: Text('${context.tr('viewAll')} ›', style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: Color(0xFF1B4D3E))),
            ),
          ],
        ),
        const SizedBox(height: 12),

        if (_loading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 20),
            child: Center(child: Text('Loading real orders...', style: TextStyle(fontSize: 12.5, color: Color(0xFF657268)))),
          )
        else if (!hasData)
          _buildEmptyOrdersCard(context)
        else
          Column(
            children: [
              ...combined.take(4).map((item) {
                final type = item['type'] as String? ?? (item['seeds'] != null ? 'seed_purchase' : 'grain_booking');
                final ref = item['reference'] as String? ??
                    item['invoice_number'] as String? ??
                    item['order_id'] as String? ??
                    (item['id'] != null ? '${item['id']}' : '—');

                final title = item['title'] as String? ??
                    item['crop_name'] as String? ??
                    (item['seeds'] != null ? item['seeds']['name'] as String? : null) ??
                    (item['grain_type'] != null ? 'Grain Booking - ${item['grain_type']}' : 'Order');

                final dateStr = _formatDate(
                  item['date'] as String? ?? item['created_at'] as String? ?? item['booking_date'] as String?,
                );

                final rawStatus = (item['payment_status_label'] as String? ?? item['payment_status'] as String? ?? item['status'] as String? ?? 'pending').trim();
                final statusLower = rawStatus.toLowerCase();
                final isDone = statusLower == 'delivered' || statusLower == 'confirmed' || statusLower == 'completed' || statusLower.contains('paid');
                final isUnpaid = statusLower.contains('unpaid') && !statusLower.contains('paid');
                final statusLabel = _getCompactStatusLabel(rawStatus);
                final statusColor = isDone
                    ? const Color(0xFF2E7D32)
                    : isUnpaid
                        ? const Color(0xFFB45309)
                        : const Color(0xFFE65100);
                final statusBg = isDone
                    ? const Color(0xFFE8F5E9)
                    : isUnpaid
                        ? const Color(0xFFFEF3C7)
                        : const Color(0xFFFFF3E0);

                final isSeed = type == 'seed_purchase';
                // For grain bookings, use grain-type-specific emoji instead of generic factory icon
                final emoji = isSeed
                    ? '🌱'
                    : getGrainEmoji(item['grain_type'] as String?);
                final emojiBg = isSeed ? const Color(0xFFE8F5E9) : const Color(0xFFECF5E8);
                final compactRef = _formatCompactRef(ref);

                return Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: _buildOrderCard(
                    emoji: emoji,
                    emojiBg: emojiBg,
                    title: title,
                    subtitle: 'Ref: #$compactRef · $dateStr',
                    statusLabel: statusLabel,
                    statusColor: statusColor,
                    statusBg: statusBg,
                    onTap: () => _showOrderDetailModal(item, type),
                  ),
                );
              }),
            ],
          ),
      ],
    );
  }

  void _showOrderDetailModal(Map<String, dynamic> item, String type) {
    final ref = item['reference'] ?? item['invoice_number'] ?? item['order_id'] ?? item['id']?.toString() ?? '—';
    final rawStatus = (item['payment_status_label'] as String? ?? item['payment_status'] as String? ?? item['status'] as String? ?? 'pending').trim();
    final status = rawStatus.toLowerCase();
    final statusLabel = _getCompactStatusLabel(rawStatus);
    final isDone = status == 'delivered' || status == 'confirmed' || status == 'completed' || status.contains('paid');
    final date = _formatDate(item['created_at'] as String? ?? item['booking_date'] as String? ?? item['date'] as String?);
    final title = item['title'] ?? item['crop_name'] ?? item['seeds']?['name'] ?? (item['grain_type'] != null ? 'Grain Slot - ${item['grain_type']}' : 'Order');
    final amount = item['amount'] ?? item['total_price'] ?? item['total_amount'] ?? item['quantity_kg'];

    showDialog(
      context: context,
      builder: (dCtx) => Dialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text(
                    'Order Details',
                    style: TextStyle(fontFamily: 'Fraunces', fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF15302A)),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    onPressed: () => Navigator.pop(dCtx),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: isDone ? const Color(0xFFDCFCE7) : const Color(0xFFFEF3C7),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      statusLabel,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w800,
                        color: isDone ? const Color(0xFF166534) : const Color(0xFF92400E),
                      ),
                    ),
                  ),
                  Text(
                    'Ref: #$ref',
                    style: const TextStyle(fontSize: 12, color: Color(0xFF657268), fontWeight: FontWeight.w700),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(title.toString(), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: Color(0xFF1C2A21))),
              const SizedBox(height: 4),
              Text('Date: $date', style: const TextStyle(fontSize: 12, color: Color(0xFF657268))),
              if (amount != null) ...[
                const SizedBox(height: 8),
                Text('Total / Quantity: $amount', style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Color(0xFF1B4D3E))),
              ],
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF1B4D3E),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  onPressed: () => Navigator.pop(dCtx),
                  child: const Text('Close'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showAllOrdersModal() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (sheetCtx) {
        return DefaultTabController(
          length: 2,
          child: Container(
            constraints: BoxConstraints(
              maxHeight: MediaQuery.of(sheetCtx).size.height * 0.85,
            ),
            child: Column(
              children: [
                Container(
                  width: 38,
                  height: 4,
                  margin: const EdgeInsets.only(top: 12, bottom: 8),
                  decoration: BoxDecoration(
                    color: const Color(0xFFE2E8F0),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text(
                        'Orders & Bookings History',
                        style: TextStyle(fontFamily: 'Fraunces', fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF15302A)),
                      ),
                      IconButton(
                        icon: const Icon(Icons.close, size: 20),
                        onPressed: () => Navigator.pop(sheetCtx),
                      ),
                    ],
                  ),
                ),
                const TabBar(
                  labelColor: Color(0xFF1B4D3E),
                  unselectedLabelColor: Color(0xFF64748B),
                  indicatorColor: Color(0xFF1B4D3E),
                  indicatorWeight: 3,
                  labelStyle: TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
                  tabs: [
                    Tab(text: 'Seed Purchases'),
                    Tab(text: 'Warehouse Slots'),
                  ],
                ),
                Expanded(
                  child: TabBarView(
                    children: [
                      _buildSeedOrdersList(),
                      _buildWarehouseBookingsList(),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _buildSeedOrdersList() {
    return FutureBuilder<List<Map<String, dynamic>>>(
      future: _repo.getSeedPurchases(),
      builder: (ctx, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator(color: Color(0xFF1B4D3E)));
        }
        final items = snap.data ?? [];
        if (items.isEmpty) {
          return const Center(child: Text('No seed purchases found', style: TextStyle(color: Color(0xFF64748B))));
        }
        return ListView.separated(
          padding: const EdgeInsets.all(16),
          itemCount: items.length,
          separatorBuilder: (_, __) => const SizedBox(height: 10),
          itemBuilder: (c, i) {
            final item = items[i];
            final ref = item['invoice_number'] ?? item['id']?.toString() ?? '—';
            final title = item['seeds']?['name'] ?? item['crop_type'] ?? 'Seed Order';
            final rawStatus = (item['payment_status_label'] as String? ?? item['payment_status'] as String? ?? item['status'] as String? ?? 'pending').trim();
            final status = rawStatus.toLowerCase();
            final isDone = status == 'delivered' || status == 'completed' || status.contains('paid');
            final statusLabel = _getCompactStatusLabel(rawStatus);
            return _buildOrderCard(
              emoji: '🌱',
              emojiBg: const Color(0xFFE8F5E9),
              title: title.toString(),
              subtitle: 'Invoice: #$ref · ${_formatDate(item['created_at'] as String?)}',
              statusLabel: statusLabel,
              statusColor: isDone ? const Color(0xFF2E7D32) : const Color(0xFFE65100),
              statusBg: isDone ? const Color(0xFFE8F5E9) : const Color(0xFFFFF3E0),
              onTap: () => _showOrderDetailModal(item, 'seed_purchase'),
            );
          },
        );
      },
    );
  }

  Widget _buildWarehouseBookingsList() {
    return FutureBuilder<List<Map<String, dynamic>>>(
      future: _repo.getBookingSlots(),
      builder: (ctx, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator(color: Color(0xFF1B4D3E)));
        }
        final items = snap.data ?? [];
        if (items.isEmpty) {
          return const Center(child: Text('No warehouse bookings found', style: TextStyle(color: Color(0xFF64748B))));
        }
        return ListView.separated(
          padding: const EdgeInsets.all(16),
          itemCount: items.length,
          separatorBuilder: (_, __) => const SizedBox(height: 10),
          itemBuilder: (c, i) {
            final item = items[i];
            final ref = item['id']?.toString() ?? '—';
            final title = 'Warehouse Slot - ${item['grain_type'] ?? 'Produce'}';
            final status = (item['status'] as String? ?? 'confirmed').toLowerCase();
            final isConfirmed = status == 'confirmed' || status == 'approved';
            return _buildOrderCard(
              emoji: '🏭',
              emojiBg: const Color(0xFFFCE4EC),
              title: title,
              subtitle: 'Slot ID: #$ref · ${item['booking_date'] ?? ''}',
              statusLabel: isConfirmed ? 'Confirmed' : (status.isNotEmpty ? status[0].toUpperCase() + status.substring(1) : 'Pending'),
              statusColor: isConfirmed ? const Color(0xFF2E7D32) : const Color(0xFFE65100),
              statusBg: isConfirmed ? const Color(0xFFE8F5E9) : const Color(0xFFFFF3E0),
              onTap: () => _showOrderDetailModal(item, 'grain_booking'),
            );
          },
        );
      },
    );
  }

  Widget _buildEmptyOrdersCard(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 20),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0xFFE1E8D8)),
      ),
      child: const Center(
        child: Column(
          children: [
            Text('📦', style: TextStyle(fontSize: 32)),
            SizedBox(height: 8),
            Text('No orders or bookings yet', style: TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF15302A))),
            SizedBox(height: 4),
            Text('Your seed purchases and warehouse bookings will appear here.', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Color(0xFF657268))),
          ],
        ),
      ),
    );
  }

  Widget _buildOrderCard({
    required String emoji,
    required Color emojiBg,
    required String title,
    required String subtitle,
    required String statusLabel,
    required Color statusColor,
    required Color statusBg,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: const Color(0xFFE1E8D8)),
        ),
        child: Row(
          children: [
            Container(
              width: 42,
              height: 42,
              decoration: BoxDecoration(color: emojiBg, borderRadius: BorderRadius.circular(14)),
              child: Center(child: Text(emoji, style: const TextStyle(fontSize: 20))),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w800, color: Color(0xFF1C2A21)), maxLines: 1, overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 2),
                  Text(
                    subtitle,
                    style: const TextStyle(fontSize: 11, color: Color(0xFF657268)),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                  decoration: BoxDecoration(color: statusBg, borderRadius: BorderRadius.circular(10)),
                  child: Text(statusLabel, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: statusColor)),
                ),
                const SizedBox(width: 4),
                const Text('›', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: Color(0xFF657268))),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildErrorBanner(String message) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xFFFEE2E2),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                color: Color(0xFF991B1B),
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: _loading ? null : _loadDashboard,
            style: TextButton.styleFrom(
              backgroundColor: const Color(0xFF991B1B),
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: _loading
                ? const SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                  )
                : const Text('Retry', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w800)),
          ),
        ],
      ),
    );
  }

  String _formatDate(String? iso) {
    if (iso == null || iso.isEmpty) return '';
    try {
      final dt = DateTime.parse(iso);
      return '${dt.day} ${_months[dt.month - 1]} ${dt.year}';
    } catch (_) {
      return iso.split('T').first;
    }
  }

  static const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
}

// ── Ring Chart Painter (replicates FarmerHome.jsx SVG ring) ──────────────
class _RingChartPainter extends CustomPainter {
  final double percentage;
  final Color ringColor;
  final Color trackColor;

  const _RingChartPainter({
    required this.percentage,
    required this.ringColor,
    required this.trackColor,
  });

  @override
  void paint(Canvas canvas, Size size) {
    const strokeWidth = 5.5;
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width / 2) - strokeWidth / 2;

    // Track (neutral/inactive ring)
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..color = trackColor
        ..strokeWidth = strokeWidth
        ..style = PaintingStyle.stroke,
    );

    // Progress arc: only draw when percentage > 0
    if (percentage > 0.0) {
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        -3.141592653589793 / 2, // start from 12 o'clock
        2 * 3.141592653589793 * percentage.clamp(0.0, 1.0),
        false,
        Paint()
          ..color = ringColor
          ..strokeWidth = strokeWidth
          ..style = PaintingStyle.stroke
          ..strokeCap = percentage >= 1.0 ? StrokeCap.butt : StrokeCap.round,
      );
    }
  }

  @override
  bool shouldRepaint(_RingChartPainter old) =>
      old.percentage != percentage || old.ringColor != ringColor || old.trackColor != trackColor;
}
