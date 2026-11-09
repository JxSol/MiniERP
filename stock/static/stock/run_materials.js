// Выпуск деталей: по выбранной детали подставляется станок и строки материалов (по позиции
// на каждую), а колонка «По норме» пересчитывается при вводе годных и брака.
document.addEventListener("DOMContentLoaded", function () {
    var $ = window.django && window.django.jQuery;
    if (!$) return;
    var table = $("#materials-data");
    if (!table.length) return;
    var partSelect = $("select[name='part']");

    function fmt(n) {
        return Number(n).toLocaleString("ru-RU", { maximumFractionDigits: 4 });
    }

    function infoUrl(partId) {
        // …/productionrun/add/ или …/productionrun/12/change/ → …/productionrun/part-info/<id>/
        var match = window.location.pathname.match(/^(.*\/productionrun\/)/);
        return match ? match[1] + "part-info/" + partId + "/" : null;
    }

    function pieces() {
        return (parseInt($("#id_good_qty").val(), 10) || 0) + (parseInt($("#id_scrap_qty").val(), 10) || 0);
    }

    function rows() {
        return table.find("tbody.form-group").not(".empty-form");
    }

    function setChoice(select, id, text) {
        // select2 (автодополнение): единственный вариант — выбранный
        select.find("option").remove();
        select.append(new Option(text, id, true, true)).trigger("change");
    }

    function updatePlanned() {
        var count = pieces();
        rows().each(function () {
            var row = $(this);
            var perPiece = parseFloat(row.data("perPiece"));
            var cell = row.find(".field-planned_display");
            if (!perPiece || !cell.length) return;
            cell.text(count ? fmt(perPiece * count) + " " + row.data("unit") : "—");
        });
    }

    function rememberNorms(info) {
        var byId = {};
        info.positions.forEach(function (p) { byId[String(p.id)] = p; });
        rows().each(function () {
            var row = $(this);
            var p = byId[row.find("input[name$='-position']").val()];
            if (p) row.data("perPiece", p.per_piece).data("unit", p.unit);
        });
        updatePlanned();
    }

    function rebuild(info) {
        // новые (несохранённые) строки убираем и строим заново по позициям детали
        rows().filter(function () { return !$(this).find("input[name$='-id']").val(); }).remove();
        $("#id_materials-TOTAL_FORMS").val(rows().length);
        var addLink = table.closest(".formset-wrapper, .inline-related").find(".add-row").get(0);
        if (!addLink) return;
        info.positions.forEach(function (p) {
            addLink.click(); // нативный клик: обработчик unfold навешен через addEventListener
            var row = rows().last();
            row.find("input[name$='-position']").val(p.id);
            row.find(".position-label").text(p.label);
            setChoice(row.find("select[name$='-material']"), p.material, p.material_text);
            row.data("perPiece", p.per_piece).data("unit", p.unit);
        });
        updatePlanned();
    }

    if (partSelect.length) {
        partSelect.on("change", function () {
            var partId = $(this).val();
            if (!partId) return;
            $.getJSON(infoUrl(partId), function (info) {
                var equipment = $("select[name='equipment']");
                if (equipment.length && !equipment.val()) setChoice(equipment, info.equipment, info.equipment_text);
                rebuild(info);
            });
        });
    }
    $("#id_good_qty, #id_scrap_qty").on("input", updatePlanned);

    var currentPart = partSelect.length ? partSelect.val() : table.data("part");
    if (currentPart && rows().length) {
        $.getJSON(infoUrl(currentPart), rememberNorms);
    }
});
