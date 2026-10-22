// Строка закупки: подсказки единиц по материалу, подстановка упаковки и цены из прошлой
// закупки у этого поставщика, пересчёт «сумма ↔ цена за упаковку» и живой итог.
document.addEventListener("DOMContentLoaded", function () {
    var $ = window.django && window.django.jQuery;
    if (!$) return;
    var infoCache = {};

    function fmt(n, digits) {
        if (n === null || n === undefined || isNaN(n)) return "";
        return Number(n).toLocaleString("ru-RU", { maximumFractionDigits: digits === undefined ? 4 : digits });
    }

    function qtyText(qty, unit) {
        if (unit === "г" && Math.abs(qty) >= 1000) return fmt(qty / 1000, 3) + " кг";
        if (unit === "мл" && Math.abs(qty) >= 1000) return fmt(qty / 1000, 3) + " л";
        return fmt(qty) + " " + unit;
    }

    function field(row, name) {
        return row.find("[name$='-" + name + "']").first();
    }

    function rowOf(el) {
        return $(el).closest("tr, .inline-related");
    }

    function infoUrl(materialId) {
        // /admin/stock/receipt/add/ или /admin/stock/receipt/12/change/ → …/receipt/material-info/<id>/
        var match = window.location.pathname.match(/^(.*\/receipt\/)/);
        return match ? match[1] + "material-info/" + materialId + "/" : null;
    }

    function loadInfo(materialId, done) {
        if (!materialId) return done(null);
        if (infoCache[materialId]) return done(infoCache[materialId]);
        var url = infoUrl(materialId);
        if (!url) return done(null);
        $.getJSON(url, function (data) {
            infoCache[materialId] = data;
            done(data);
        });
    }

    function unitHint(input, text) {
        var hint = input.next(".unit-hint");
        if (!hint.length) {
            hint = $("<span class='unit-hint text-xs text-base-500 ml-1 whitespace-nowrap'></span>");
            input.after(hint);
        }
        hint.text(text || "");
    }

    function applyMode(row) {
        var mode = field(row, "mode").val();
        var packsMode = mode !== "direct";
        field(row, "packs").prop("disabled", !packsMode).css("opacity", packsMode ? 1 : 0.4);
        field(row, "pack_size").prop("disabled", !packsMode).css("opacity", packsMode ? 1 : 0.4);
        field(row, "quantity_entered").prop("disabled", packsMode).css("opacity", packsMode ? 0.4 : 1);
    }

    function offerFor(info, row) {
        var supplier = field(row, "supplier").val() || "";
        var manufacturer = field(row, "manufacturer").val() || "";
        var offers = (info && info.offers) || [];
        var exact = offers.filter(function (o) {
            return String(o.supplier || "") === String(supplier) && String(o.manufacturer || "") === String(manufacturer);
        });
        return exact[0] || null;
    }

    function prefill(row, info, force) {
        if (!info) return;
        unitHint(field(row, "pack_size"), info.unit + " в уп.");
        unitHint(field(row, "quantity_entered"), info.direct_unit);
        unitHint(field(row, "pack_price"), "₸");
        var offer = offerFor(info, row);
        var packSize = field(row, "pack_size"), price = field(row, "pack_price");
        if (force || !packSize.val()) {
            var size = offer ? offer.pack_size : info.default_pack_size;
            if (size) packSize.val(size);
        }
        if (offer && (force || !price.val())) price.val(offer.pack_price);
        if (force && !row.data("saved")) field(row, "mode").val(info.default_mode);
        applyMode(row);
    }

    function recalc(row, source) {
        var info = infoCache[field(row, "material").val()];
        var mode = field(row, "mode").val();
        var count = mode === "direct" ? parseFloat(field(row, "quantity_entered").val()) : parseFloat(field(row, "packs").val());
        var priceEach = field(row, "pack_price"), total = field(row, "total_price");
        if (count > 0) {
            if (source === "total" && total.val()) {
                priceEach.val((parseFloat(total.val()) / count).toFixed(2).replace(/\.?0+$/, ""));
            } else if (priceEach.val()) {
                total.val(Math.round(parseFloat(priceEach.val()) * count));
            } else if (total.val()) {
                priceEach.val((parseFloat(total.val()) / count).toFixed(2).replace(/\.?0+$/, ""));
            }
        }
        var cell = row.find(".field-summary_display, td.summary_display").first();
        if (!cell.length || !info) return;
        var qty = mode === "direct"
            ? count * parseFloat(info.factor || 1)
            : count * parseFloat(field(row, "pack_size").val());
        if (!(qty > 0)) { cell.text("—"); return; }
        var parts = [];
        parts.push(mode === "direct"
            ? fmt(count) + " " + info.direct_unit + (info.factor && info.factor !== "1" ? " = " + qtyText(qty, info.unit) : "")
            : fmt(count) + " уп. × " + qtyText(parseFloat(field(row, "pack_size").val()), info.unit) + " = " + qtyText(qty, info.unit));
        if (info.alt_unit && info.alt_factor) parts[0] += " (" + fmt(qty * parseFloat(info.alt_factor)) + " " + info.alt_unit + ")";
        var totalValue = parseFloat(total.val());
        if (totalValue > 0) {
            var perUnit = totalValue / qty;
            parts.push(fmt(perUnit, 4) + " ₸/" + info.unit);
            if (info.alt_unit && info.alt_factor) parts.push(fmt(perUnit / parseFloat(info.alt_factor), 4) + " ₸/" + info.alt_unit);
        }
        cell.text(parts.join(" · "));
    }

    function init(row, force) {
        var materialId = field(row, "material").val();
        loadInfo(materialId, function (info) {
            prefill(row, info, force);
            recalc(row);
        });
    }

    $(document).on("change", "select[name$='-material']", function () {
        var row = rowOf(this);
        row.data("saved", !!row.find("input[name$='-id']").val());
        init(row, true);
    });
    $(document).on("change", "select[name$='-supplier'], select[name$='-manufacturer']", function () {
        var row = rowOf(this);
        loadInfo(field(row, "material").val(), function (info) { prefill(row, info, true); recalc(row); });
    });
    $(document).on("change", "select[name$='-mode']", function () {
        var row = rowOf(this);
        applyMode(row);
        recalc(row);
    });
    $(document).on("input", "input[name$='-packs'], input[name$='-pack_size'], input[name$='-quantity_entered'], input[name$='-pack_price']", function () {
        recalc(rowOf(this), "each");
    });
    $(document).on("input", "input[name$='-total_price']", function () {
        recalc(rowOf(this), "total");
    });
    $(document).on("formset:added", function (event, $row) {
        var row = $row && $row.length ? $row : $(event.target);
        applyMode(row);
    });

    $("select[name$='-material']").each(function () {
        var row = rowOf(this);
        row.data("saved", !!row.find("input[name$='-id']").val());
        if ($(this).val()) init(row, false); else applyMode(row);
    });
});
