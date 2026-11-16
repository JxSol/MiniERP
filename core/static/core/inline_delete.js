// Удаление новой, ещё не сохранённой строки в таблицах карточек (позиции закупки, состав
// модели, фото и остальные).
//
// После удаления строки ниже нужно перенумеровать: поля строки называются «lines-2-material»,
// и номера должны идти подряд. Тема (unfold 0.110) делает это так: берёт HTML строки как текст,
// меняет в нём номер и вставляет обратно. Строка при этом создаётся заново, и всё введённое
// в ней пропадает — числа, подписи, выбранные файлы, — а выпадающие списки с поиском перестают
// открываться.
//
// Здесь то же удаление без пересоздания строк: меняются только номера в именах и идентификаторах,
// как это делает сама админка Django. Скрипт перехватывает щелчок по корзине раньше темы.
(function () {
    "use strict";

    // Атрибуты, в которых встречается номер строки; ещё все aria-*. Атрибуты Alpine (x-ref,
    // x-anchor) не трогаем: их имена нужны только внутри своей строки и остаются согласованными
    const RENUMBERED = ["id", "name", "for", "data-id", "data-inline-formset"];

    function jQueryOfAdmin() {
        return window.django && window.django.jQuery;
    }

    // Выпадающий список с поиском (select2) хранит своё состояние под ключом из идентификатора
    // поля. Ключ переезжает вместе с идентификатором, иначе новая строка с тем же номером
    // получила бы чужое состояние
    function moveSelect2State(element, oldId, newId) {
        if (element.getAttribute("data-select2-id") !== oldId) return;
        const $ = jQueryOfAdmin();
        const amd = $ && $.fn.select2 && $.fn.select2.amd;
        const cache = amd && amd.require("select2/utils").__cache;
        if (!cache || !(oldId in cache)) return;
        cache[newId] = cache[oldId];
        delete cache[oldId];
        element.setAttribute("data-select2-id", newId);
    }

    function renumber(root, pattern, replacement) {
        const $ = jQueryOfAdmin();
        [root].concat(Array.from(root.querySelectorAll("*"))).forEach(function (element) {
            Array.from(element.attributes).forEach(function (attribute) {
                const name = attribute.name;
                if (!RENUMBERED.includes(name) && !name.startsWith("aria-")) return;
                const old = attribute.value;
                const value = old.replace(pattern, replacement);
                if (value === old) return;
                element.setAttribute(name, value);
                if (name === "id") moveSelect2State(element, old, value);
                // jQuery запоминает data-атрибут при первом чтении — пусть прочитает заново
                if (name.startsWith("data-") && $) $(element).removeData(name.slice(5));
            });
            // Меню «⋮» у выпадающего списка тема переносит в конец страницы (x-teleport):
            // его ссылки «Добавить», «Изменить» находят своё поле по идентификатору
            if (element.tagName === "TEMPLATE" && element._x_teleport) {
                renumber(element._x_teleport, pattern, replacement);
            }
        });
    }

    function ownRows(formset) {
        return Array.from(formset.querySelectorAll(":scope > .form-group:not(.empty-form)"));
    }

    function counter(wrapper, name) {
        return wrapper.querySelector("input[type=hidden][name$='" + name + "']");
    }

    // Мелочи оформления после удаления делает сама тема: рамка последней строки, пустая
    // таблица, счётчик в заголовках
    function theme(name, argument) {
        if (typeof window[name] === "function") window[name](argument);
    }

    function removeRow(button) {
        const row = button.closest(".form-group");
        const formset = button.closest(".formset");
        const wrapper = button.closest(".formset-wrapper");
        if (!row || !formset || !wrapper) return;
        const prefix = JSON.parse(formset.dataset.inlineFormset).options.prefix;
        const total = counter(wrapper, "TOTAL_FORMS");
        const min = counter(wrapper, "MIN_NUM_FORMS");
        const max = counter(wrapper, "MAX_NUM_FORMS");
        if (Number(total.value) === Number(min.value)) return; // обязательные строки не удаляются

        const parent = row.parentElement;
        row.remove();

        // «id_lines-3-material» → «id_lines-2-material»: номер сразу после названия таблицы.
        // Перед названием — начало значения или знак, но не буква: «sublines-3» не трогаем
        const escaped = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const pattern = new RegExp("(^|[^A-Za-z0-9])" + escaped + "-\\d+", "g");
        const rows = ownRows(formset);
        rows.forEach((other, index) => renumber(other, pattern, "$1" + prefix + "-" + index));
        total.value = rows.length;

        if (rows.length < Number(max.value)) {
            const links = Array.from(formset.querySelectorAll(".add-row"));
            const link = links.find((candidate) => candidate.closest(".formset") === formset);
            if (link) link.classList.remove("hidden");
        }
        theme("updateStackedCounter", parent);
        theme("applyLastClass", formset);
        theme("checkFormsetEmpty", formset);

        document.dispatchEvent(
            new CustomEvent("formset:removed", { detail: { formsetName: prefix } })
        );
    }

    document.addEventListener(
        "click",
        function (event) {
            const target = event.target;
            const element = target.nodeType === Node.ELEMENT_NODE ? target : target.parentElement;
            const button = element && element.closest(".delete-template");
            if (!button) return;
            event.preventDefault();
            event.stopImmediatePropagation(); // до обработчика темы щелчок не доходит
            removeRow(button);
        },
        true
    );
})();
