// Подсказки при наведении: текст справки, который показывается под полем,
// дублируется в title подписи поля (и у чекбоксов), чтобы описание было видно,
// просто наведя курсор на название поля.
(function () {
    function apply(root) {
        root.querySelectorAll("div.leading-relaxed.mt-2.text-xs").forEach(function (help) {
            var text = help.textContent.replace(/\s+/g, " ").trim();
            if (!text) return;
            var label = null;
            var valueBox = help.closest(".grow.relative");
            if (valueBox && valueBox.previousElementSibling) {
                label = valueBox.previousElementSibling.querySelector("label");
            }
            if (!label) {
                var container = help.closest(".form-checkbox, .form-field");
                label = container ? container.querySelector("label") : null;
            }
            if (label && !label.title) {
                label.title = text;
                label.classList.add("cursor-help");
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () { apply(document); });
    // строки инлайнов, добавленные кнопкой «Добавить ещё»
    document.addEventListener("formset:added", function (event) { apply(event.target || document); });
})();
