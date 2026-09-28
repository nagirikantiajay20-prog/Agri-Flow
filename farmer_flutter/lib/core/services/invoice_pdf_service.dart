import 'dart:io';
import 'package:flutter/material.dart';
import 'package:open_file/open_file.dart';
import 'package:path_provider/path_provider.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;
import 'package:printing/printing.dart';

class InvoicePdfService {
  static Future<bool> generateAndOpenInvoicePdf({
    required BuildContext context,
    required Map<String, dynamic> invoiceData,
  }) async {
    try {
      final pdf = pw.Document();

      final invoiceNum = (invoiceData['invoiceNumber'] as String?) ??
          (invoiceData['invoice_number'] as String?) ??
          'INV-UNKNOWN';
      final date = (invoiceData['date'] as String?) ??
          (invoiceData['created_at'] as String?) ??
          'N/A';
      final paymentStatus = (invoiceData['paymentStatus'] as String?) ??
          (invoiceData['payment_status'] as String?) ??
          'Pending (Pay at Warehouse)';
      final seedName = (invoiceData['seedName'] as String?) ??
          (invoiceData['seed_name'] as String?) ??
          (invoiceData['seed']?['name'] as String?) ??
          'Seed Product';
      final variety = (invoiceData['variety'] as String?) ??
          (invoiceData['seed']?['variety'] as String?) ??
          'Standard Variety';
      final qtyKg = (invoiceData['quantityKg'] as num?)?.toDouble() ??
          (invoiceData['quantity_kg'] as num?)?.toDouble() ??
          1.0;
      final unitPrice = (invoiceData['unitPrice'] as num?)?.toDouble() ??
          (invoiceData['price_per_kg'] as num?)?.toDouble() ??
          0.0;
      final totalAmount = (invoiceData['totalAmount'] as num?)?.toDouble() ??
          (invoiceData['total_amount'] as num?)?.toDouble() ??
          (qtyKg * unitPrice);

      pdf.addPage(
        pw.Page(
          pageFormat: PdfPageFormat.a4,
          margin: const pw.EdgeInsets.all(32),
          build: (pw.Context pdfContext) {
            return pw.Column(
              crossAxisAlignment: pw.CrossAxisAlignment.start,
              children: [
                // Header
                pw.Row(
                  mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
                  children: [
                    pw.Column(
                      crossAxisAlignment: pw.CrossAxisAlignment.start,
                      children: [
                        pw.Text(
                          'Sri Siva Sai Seeds',
                          style: pw.TextStyle(
                            fontSize: 22,
                            fontWeight: pw.FontWeight.bold,
                            color: PdfColors.green900,
                          ),
                        ),
                        pw.Text(
                          'FARMER PORTAL INVOICE',
                          style: const pw.TextStyle(
                            fontSize: 10,
                            color: PdfColors.grey700,
                          ),
                        ),
                      ],
                    ),
                    pw.Container(
                      padding: const pw.EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      decoration: pw.BoxDecoration(
                        color: PdfColors.green100,
                        borderRadius: pw.BorderRadius.circular(6),
                      ),
                      child: pw.Text(
                        'OFFICIAL INVOICE',
                        style: pw.TextStyle(
                          fontSize: 12,
                          fontWeight: pw.FontWeight.bold,
                          color: PdfColors.green900,
                        ),
                      ),
                    ),
                  ],
                ),
                pw.SizedBox(height: 16),
                pw.Divider(color: PdfColors.orange800, thickness: 2),
                pw.SizedBox(height: 16),

                // Key Meta Info Grid
                pw.Row(
                  mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
                  children: [
                    pw.Column(
                      crossAxisAlignment: pw.CrossAxisAlignment.start,
                      children: [
                        pw.Text('INVOICE NUMBER',
                            style: pw.TextStyle(
                                fontSize: 9,
                                fontWeight: pw.FontWeight.bold,
                                color: PdfColors.grey700)),
                        pw.Text(invoiceNum,
                            style: pw.TextStyle(
                                fontSize: 13,
                                fontWeight: pw.FontWeight.bold)),
                        pw.SizedBox(height: 10),
                        pw.Text('DATE & TIME',
                            style: pw.TextStyle(
                                fontSize: 9,
                                fontWeight: pw.FontWeight.bold,
                                color: PdfColors.grey700)),
                        pw.Text(date,
                            style: const pw.TextStyle(fontSize: 11)),
                      ],
                    ),
                    pw.Column(
                      crossAxisAlignment: pw.CrossAxisAlignment.start,
                      children: [
                        pw.Text('PAYMENT STATUS',
                            style: pw.TextStyle(
                                fontSize: 9,
                                fontWeight: pw.FontWeight.bold,
                                color: PdfColors.grey700)),
                        pw.Text(paymentStatus,
                            style: pw.TextStyle(
                                fontSize: 12,
                                fontWeight: pw.FontWeight.bold,
                                color: PdfColors.deepOrange700)),
                        pw.SizedBox(height: 10),
                        pw.Text('TOTAL AMOUNT DUE',
                            style: pw.TextStyle(
                                fontSize: 9,
                                fontWeight: pw.FontWeight.bold,
                                color: PdfColors.grey700)),
                        pw.Text('INR ${totalAmount.toStringAsFixed(2)}',
                            style: pw.TextStyle(
                                fontSize: 15,
                                fontWeight: pw.FontWeight.bold,
                                color: PdfColors.green900)),
                      ],
                    ),
                  ],
                ),
                pw.SizedBox(height: 24),

                // Table Header
                pw.Container(
                  padding: const pw.EdgeInsets.all(8),
                  color: PdfColors.grey200,
                  child: pw.Row(
                    children: [
                      pw.Expanded(
                          flex: 3,
                          child: pw.Text('PRODUCT & VARIETY',
                              style: pw.TextStyle(
                                  fontSize: 10,
                                  fontWeight: pw.FontWeight.bold))),
                      pw.Expanded(
                          flex: 1,
                          child: pw.Text('QTY (KG)',
                              style: pw.TextStyle(
                                  fontSize: 10,
                                  fontWeight: pw.FontWeight.bold),
                              textAlign: pw.TextAlign.right)),
                      pw.Expanded(
                          flex: 1,
                          child: pw.Text('PRICE/KG',
                              style: pw.TextStyle(
                                  fontSize: 10,
                                  fontWeight: pw.FontWeight.bold),
                              textAlign: pw.TextAlign.right)),
                      pw.Expanded(
                          flex: 1,
                          child: pw.Text('AMOUNT',
                              style: pw.TextStyle(
                                  fontSize: 10,
                                  fontWeight: pw.FontWeight.bold),
                              textAlign: pw.TextAlign.right)),
                    ],
                  ),
                ),

                // Table Row
                pw.Container(
                  padding: const pw.EdgeInsets.all(8),
                  child: pw.Row(
                    children: [
                      pw.Expanded(
                        flex: 3,
                        child: pw.Column(
                          crossAxisAlignment: pw.CrossAxisAlignment.start,
                          children: [
                            pw.Text(seedName,
                                style: pw.TextStyle(
                                    fontSize: 11,
                                    fontWeight: pw.FontWeight.bold)),
                            pw.Text(variety,
                                style: const pw.TextStyle(
                                    fontSize: 9, color: PdfColors.grey700)),
                          ],
                        ),
                      ),
                      pw.Expanded(
                          flex: 1,
                          child: pw.Text('${qtyKg.toStringAsFixed(1)} kg',
                              style: const pw.TextStyle(fontSize: 10),
                              textAlign: pw.TextAlign.right)),
                      pw.Expanded(
                          flex: 1,
                          child: pw.Text('INR ${unitPrice.toStringAsFixed(2)}',
                              style: const pw.TextStyle(fontSize: 10),
                              textAlign: pw.TextAlign.right)),
                      pw.Expanded(
                          flex: 1,
                          child: pw.Text(
                              'INR ${totalAmount.toStringAsFixed(2)}',
                              style: pw.TextStyle(
                                  fontSize: 10,
                                  fontWeight: pw.FontWeight.bold),
                              textAlign: pw.TextAlign.right)),
                    ],
                  ),
                ),

                pw.Divider(color: PdfColors.grey400),
                pw.SizedBox(height: 16),

                // Total Summary
                pw.Row(
                  mainAxisAlignment: pw.MainAxisAlignment.end,
                  children: [
                    pw.Text('NET PAYABLE: ',
                        style: pw.TextStyle(
                            fontSize: 12, fontWeight: pw.FontWeight.bold)),
                    pw.Text('INR ${totalAmount.toStringAsFixed(2)}',
                        style: pw.TextStyle(
                            fontSize: 14,
                            fontWeight: pw.FontWeight.bold,
                            color: PdfColors.green900)),
                  ],
                ),

                pw.Spacer(),

                // Footer Notice
                pw.Container(
                  padding: const pw.EdgeInsets.all(12),
                  decoration: pw.BoxDecoration(
                    color: PdfColors.orange50,
                    border: pw.Border.all(color: PdfColors.orange200),
                    borderRadius: pw.BorderRadius.circular(6),
                  ),
                  child: pw.Row(
                    children: [
                      pw.Text('NOTE: ',
                          style: pw.TextStyle(
                              fontSize: 9,
                              fontWeight: pw.FontWeight.bold,
                              color: PdfColors.orange900)),
                      pw.Expanded(
                        child: pw.Text(
                          'Payment must be completed at the designated Sri Siva Sai Seeds warehouse when collecting seed stock.',
                          style: const pw.TextStyle(
                              fontSize: 9, color: PdfColors.orange900),
                        ),
                      ),
                    ],
                  ),
                ),
                pw.SizedBox(height: 10),
                pw.Center(
                  child: pw.Text(
                    'Thank you for partnering with Sri Siva Sai Seeds.',
                    style: const pw.TextStyle(
                        fontSize: 9, color: PdfColors.grey600),
                  ),
                ),
              ],
            );
          },
        ),
      );

      final bytes = await pdf.save();

      // Save to local device storage
      final safeInvoiceName =
          invoiceNum.replaceAll(RegExp(r'[^a-zA-Z0-9\-_]'), '_');
      final outputDir = await getApplicationDocumentsDirectory();
      final filePath = '${outputDir.path}/Invoice_$safeInvoiceName.pdf';
      final file = File(filePath);
      await file.writeAsBytes(bytes);

      if (!await file.exists()) {
        throw Exception('Failed to write invoice PDF file to storage.');
      }

      // Primary: use the system share sheet which reliably opens PDF viewers
      // on both Android and iOS. OpenFile.open() fails silently on Android
      // for files in the app-internal Documents directory without a
      // FileProvider content:// URI setup — causing the "blank page" bug.
      bool opened = false;
      try {
        // Try OpenFile first (direct open, nicer UX on iOS & some Androids)
        final openResult = await OpenFile.open(file.path);
        opened = openResult.type == ResultType.done;
      } catch (_) {
        opened = false;
      }

      if (!opened) {
        // Fallback to system share-sheet PDF viewer — always works
        await Printing.sharePdf(
            bytes: bytes, filename: 'Invoice_$safeInvoiceName.pdf');
      }


      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Invoice saved: Invoice_$safeInvoiceName.pdf'),
            backgroundColor: const Color(0xFF1B4D3E),
            duration: const Duration(seconds: 4),
          ),
        );
      }
      return true;
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Could not download invoice: ${e.toString()}'),
            backgroundColor: const Color(0xFFD9704C),
          ),
        );
      }
      return false;
    }
  }
}
