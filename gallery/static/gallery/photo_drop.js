// Вкладка «Фото»: файлы перетаскивают в карточку, вставляют из буфера обмена (Ctrl+V) или
// выбирают сразу несколько. На каждый файл в таблицу фото добавляется обычная строка, как от
// ссылки «Добавить ещё», и файл кладётся в её поле. Поэтому тип и подпись можно задать сразу,
// проверки на сервере те же, а сохраняются фото вместе с карточкой.
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    const zone = document.querySelector("[data-photo-drop]");
    const table = zone && document.getElementById(zone.dataset.photoDrop + "-data");
    if (!table) return;
    try {
        new DataTransfer(); // без него файл в поле не положить: очень старый браузер
    } catch (error) {
        zone.hidden = true;
        return;
    }

    const prefix = zone.dataset.photoDrop;
    const maxMb = Number(zone.dataset.maxMb);
    const extensions = zone.dataset.extensions.split(",");
    const status = zone.querySelector(".photo-drop-status");
    const formats = "JPG, PNG, WEBP или HEIC";

    // --- Строки таблицы фото -----------------------------------------------------------------

    function rows() {
        return Array.from(table.querySelectorAll(":scope > .form-group:not(.empty-form)"));
    }

    function isNew(row) {
        return row.classList.contains("template");
    }

    function fileField(row) {
        return row.querySelector("input[type=file]");
    }

    function orderField(row) {
        return row.querySelector("input[name$='-order']");
    }

    // Новая строка — той же ссылкой «Добавить ещё», что нажимает человек: тема сама расставит
    // номера полей и счётчик строк. null — строка не появилась (достигнут предел числа фото)
    function addRow() {
        const template = table.querySelector(":scope > .empty-form");
        const link = table.querySelector(".add-row");
        if (!template || !link) return null;
        const last = template.previousElementSibling;
        // щелчок по надписи внутри ссылки, как у человека: тогда на пределе тема прячет ссылку
        (link.querySelector("span") || link).click();
        const row = template.previousElementSibling;
        return row && row !== last && row.matches(".form-group") ? row : null;
    }

    // Миниатюра выбранного, но ещё не сохранённого файла. Прежнее содержимое ячейки (сохранённая
    // миниатюра или прочерк) запоминается и возвращается, если выбор файла отменили
    function showPreview(row, file) {
        const cell = row.querySelector(".field-preview .readonly");
        if (!cell) return;
        const old = cell.querySelector("img.photo-drop-preview");
        if (old) URL.revokeObjectURL(old.src);
        if (!file) {
            if ("saved" in cell.dataset) cell.innerHTML = cell.dataset.saved;
            return;
        }
        if (!("saved" in cell.dataset)) cell.dataset.saved = cell.innerHTML;
        const image = document.createElement("img");
        image.className = "photo-drop-preview";
        image.alt = "";
        image.draggable = false;
        image.addEventListener("error", function () {
            // HEIC браузер показать не может: сервер переведёт его в JPEG при сохранении
            const box = document.createElement("span");
            box.className = "photo-drop-preview";
            box.textContent = extensionOf(file).toUpperCase();
            image.replaceWith(box);
            URL.revokeObjectURL(image.src);
        });
        image.src = URL.createObjectURL(file);
        cell.replaceChildren(image);
    }

    // Строка после выбора файла: имя файла в поле, миниатюра и место в конце галереи.
    // Порядок ставится только строке с файлом: пустую строку Django при сохранении пропускает,
    // пока в ней ничего не меняли
    function refresh(row) {
        const file = fileField(row).files[0];
        const name = row.querySelector(".field-image input[type=text]");
        if (name && file) name.setAttribute("value", file.name);
        showPreview(row, file);
        const order = orderField(row);
        if (!order || !isNew(row)) return;
        if (!file) {
            order.value = 0;
        } else if (!Number(order.value)) {
            const others = rows()
                .filter((other) => other !== row)
                .map((other) => Number((orderField(other) || {}).value) || 0);
            order.value = Math.max(-1, ...others) + 1;
        }
    }

    function put(row, file) {
        const field = fileField(row);
        const transfer = new DataTransfer();
        transfer.items.add(file);
        field.files = transfer.files;
        refresh(row);
        // По событию input тема запоминает, что в карточке есть несохранённое, и предупреждает
        // при уходе со страницы. На поля новой строки она подписывается чуть позже её появления
        setTimeout(() => field.dispatchEvent(new Event("input", { bubbles: true })), 0);
    }

    // --- Добавление файлов -------------------------------------------------------------------

    function extensionOf(file) {
        return file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    }

    // Почему файл не подходит (те же правила, что на сервере); пусто — подходит
    function problem(file) {
        if (!extensions.includes(extensionOf(file))) return "это не " + formats;
        if (file.size > maxMb * 1024 * 1024) return "больше " + maxMb + " МБ";
        return "";
    }

    function showTab() {
        const tab = document.querySelector('#tabs-items a[href="#' + prefix + '"]');
        if (tab && !tab.classList.contains("active")) tab.click();
    }

    function report(added, rejected) {
        status.replaceChildren();
        function line(text, className) {
            const element = document.createElement("div");
            element.textContent = text;
            if (className) element.className = className;
            status.append(element);
        }
        if (added) {
            line("Добавлено фото: " + added + ". Осталось сохранить карточку — кнопка «Сохранить» внизу.");
        }
        if (rejected.length) line("Не добавлено: " + rejected.join("; ") + ".", "photo-drop-rejected");
    }

    function add(files) {
        showTab();
        if (document.readyState !== "complete") {
            report(0, ["страница ещё загружается, попробуйте ещё раз"]);
            return;
        }
        if (!files.length) {
            report(0, [
                "файл получить не удалось. Перетащите файл с компьютера или скопируйте картинку " +
                    "и вставьте её клавишами Ctrl+V",
            ]);
            return;
        }
        // Сначала заполняются пустые новые строки: например, после ошибки сохранения файлы
        // из строк пропадают, а тип и подпись остаются
        const empty = rows().filter((row) => isNew(row) && !fileField(row).files.length);
        const rejected = [];
        let added = 0;
        let last = null;
        let full = 0;
        files.forEach(function (file) {
            const why = problem(file);
            if (why) {
                rejected.push("«" + file.name + "» — " + why);
                return;
            }
            const row = empty.shift() || addRow();
            if (!row) {
                full += 1;
                return;
            }
            put(row, file);
            added += 1;
            last = row;
        });
        if (full) {
            const limit = document.getElementById("id_" + prefix + "-MAX_NUM_FORMS").value;
            rejected.push("ещё " + full + " фото — в одной карточке их не больше " + limit);
        }
        report(added, rejected);
        if (last) last.scrollIntoView({ block: "nearest" });
    }

    // --- Выбор файлов по щелчку ---------------------------------------------------------------

    function choose() {
        const picker = document.createElement("input");
        picker.type = "file";
        picker.multiple = true;
        picker.accept = "image/*," + extensions.map((extension) => "." + extension).join(",");
        picker.addEventListener("change", () => add(Array.from(picker.files)));
        picker.click();
    }

    zone.addEventListener("click", choose);
    zone.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        choose();
    });

    // Файл выбрали обычной кнопкой в строке — та же миниатюра и то же место в конце
    table.addEventListener("change", function (event) {
        if (event.target.matches("input[type=file]")) refresh(event.target.closest(".form-group"));
    });

    // --- Вставка из буфера обмена -------------------------------------------------------------

    // Картинка из буфера обмена всегда называется image.png — даём ей имя по времени вставки
    function named(file, index) {
        if (file.name !== "image.png") return file;
        const now = new Date();
        const two = (number) => String(number).padStart(2, "0");
        const stamp =
            now.getFullYear() + two(now.getMonth() + 1) + two(now.getDate()) + "-" +
            two(now.getHours()) + two(now.getMinutes()) + two(now.getSeconds());
        const name = "foto-" + stamp + (index ? "-" + (index + 1) : "") + ".png";
        return new File([file], name, { type: file.type });
    }

    document.addEventListener("paste", function (event) {
        const data = event.clipboardData;
        const files = data ? Array.from(data.files) : [];
        if (!files.length) return;
        // Курсор стоит в поле ввода, а в буфере есть текст — значит, вставляют текст: ячейка
        // из Excel, например, копируется сразу и текстом, и картинкой
        const field = elementOf(event).closest("input, textarea, [contenteditable]");
        if (field && data.getData("text/plain")) return;
        event.preventDefault();
        add(files.map(named));
    });

    // --- Перетаскивание -----------------------------------------------------------------------

    const overlay = document.createElement("div");
    overlay.className = "photo-drop-overlay";
    overlay.append(document.createElement("span"));
    overlay.firstChild.textContent = "Отпустите — фото добавятся в карточку";
    document.body.append(overlay);

    function highlight(on) {
        if (overlay.classList.contains("is-active") !== on) overlay.classList.toggle("is-active", on);
    }

    function elementOf(event) {
        const target = event.target;
        return target.nodeType === Node.ELEMENT_NODE ? target : target.parentElement || document.body;
    }

    // Тащат что-то с самой страницы (строку таблицы, миниатюру), а не файл с компьютера
    let ownDrag = false;
    document.addEventListener("dragstart", () => (ownDrag = true));
    document.addEventListener("dragend", () => (ownDrag = false));

    // Файлы с компьютера, и не над полем другого файла (у детали это «Файл модели»):
    // туда файл попадает как обычно
    function forPhotos(event) {
        if (ownDrag || !event.dataTransfer) return false;
        if (!Array.from(event.dataTransfer.types).includes("Files")) return false;
        const field = elementOf(event).closest("input[type=file]");
        return !field || table.contains(field);
    }

    // Пока файлы тащат, браузер сообщает только их тип. Рамку не показываем, если фото
    // среди них точно нет
    function mayBePhotos(event) {
        const items = Array.from(event.dataTransfer.items || []);
        return !items.length || items.some((item) => !item.type || item.type.startsWith("image/"));
    }

    document.addEventListener("dragover", function (event) {
        const ours = forPhotos(event);
        highlight(ours && mayBePhotos(event));
        if (!ours) return;
        // Иначе браузер не отдаст файлы странице, а откроет их вместо неё — и несохранённое
        // в карточке пропадёт
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
    });

    // Рамка убирается, когда курсор ушёл за край окна: у переходов между элементами страницы
    // координаты внутри окна, и на них рамка не мигает
    document.addEventListener("dragleave", function (event) {
        const x = event.clientX;
        const y = event.clientY;
        if (x <= 0 || y <= 0 || x >= window.innerWidth || y >= window.innerHeight) highlight(false);
    });

    // Пока что-то тащат, мышь молчит: раз она двигается, перетаскивание закончилось или отменено
    document.addEventListener("mousemove", () => highlight(false));

    document.addEventListener("drop", function (event) {
        const ours = forPhotos(event);
        ownDrag = false;
        highlight(false);
        if (!ours) return;
        event.preventDefault();
        add(Array.from(event.dataTransfer.files));
    });

    // Удалить лишнее фото до сохранения можно корзиной в строке: выбранные файлы, типы и подписи
    // в остальных строках при этом остаются (core/inline_delete.js)
});
