// Поле «дни / часы / минуты / секунды»: при потере фокуса переполнение переносится
// в старший разряд (1 ч 70 мин → 2 ч 10 мин), отрицательные и дробные значения убираются.
(function () {
    var LIMITS = { seconds: 60, minutes: 60, hours: 24, days: null };
    var ORDER = ["seconds", "minutes", "hours", "days"];

    function normalize(container) {
        var inputs = {};
        container.querySelectorAll("input[data-part]").forEach(function (input) {
            inputs[input.dataset.part] = input;
        });
        var values = {};
        ORDER.forEach(function (part) {
            var raw = inputs[part] ? inputs[part].value.replace(",", ".") : "";
            var n = parseFloat(raw);
            values[part] = isNaN(n) || n < 0 ? 0 : Math.round(n);
        });
        for (var i = 0; i < ORDER.length - 1; i++) {
            var part = ORDER[i], next = ORDER[i + 1], limit = LIMITS[part];
            if (values[part] >= limit) {
                values[next] += Math.floor(values[part] / limit);
                values[part] = values[part] % limit;
            }
        }
        var empty = ORDER.every(function (part) { return values[part] === 0; });
        ORDER.forEach(function (part) {
            if (!inputs[part]) return;
            inputs[part].value = empty ? "" : values[part];
        });
    }

    function onEvent(event) {
        var input = event.target;
        if (!(input instanceof HTMLInputElement) || !input.dataset.part) return;
        var container = input.closest(".duration-widget");
        if (container) normalize(container);
    }

    document.addEventListener("focusout", onEvent);
    document.addEventListener("change", onEvent);
})();
