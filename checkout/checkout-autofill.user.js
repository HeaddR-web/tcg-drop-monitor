// ==UserScript==
// @name         Checkout Autofill Blitz
// @namespace    local.checkout.autofill
// @version      2.0
// @description  Füllt Checkout-Formulare per Hotkey (Strg+Shift+1) aus – optimiert für Magento/Shops wie Steiff
// @match        *://*.steiff.com/*
// @match        *://*.teddys-rothenburg.de/*
// @match        *://*.sammlerkontor.de/*
// @match        *://*.galerista.de/*
// @match        *://*.card-corner.de/*
// @match        *://*.geeksheaven.de/*
// @match        *://*.feenturm.de/*
// @match        *://*.tcg-trade.de/*
// @match        *://*.mueller.de/*
// @match        *://*.mediamarkt.de/*
// @match        *://*.saturn.de/*
// @match        *://*.cardsforall.de/*
// (weitere Shops einfach als @match-Zeile ergaenzen)
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    /* ========================================================================
     * DEINE DATEN – HIER EINTRAGEN
     * ======================================================================*/
    const PROFIL = {
        anrede:       "Herr",            // "Herr" / "Frau" / "" (leer lassen zum Überspringen)
        vorname:      "Max",
        nachname:     "Mustermann",
        email:        "max@example.com",
        telefon:      "+49 170 1234567",
        strasse:      "Musterstraße",
        hausnummer:   "12",
        plz:          "12345",
        stadt:        "Berlin",
        land:         "Deutschland",     // für Dropdowns: "Deutschland", "Österreich", "Schweiz"
        geburtsdatum: "01.01.1990",      // Format je nach Shop: TT.MM.JJJJ
    };

    /* ========================================================================
     * EINSTELLUNGEN
     * ======================================================================*/
    const EINSTELLUNGEN = {
        autoWeiterKlick: false,     // true = "Weiter"-Button wird nach dem Ausfüllen geklickt
        zeigeButton: true,          // schwebenden ⚡-Button anzeigen
        autoModus: false,           // bewusst AUS: du entscheidest wann gefuellt wird (Hotkey oder Button)
        zahlart: "paypal",          // "" = aus | "paypal" = PayPal-Radio im Zahlungsschritt automatisch wählen
    };

    /* ========================================================================
     * SHOP-SPEZIFISCHE SELEKTOREN
     * steiff.com läuft auf Magento 2 → Standard-Magento-Feldnamen hinterlegt.
     * Weitere Shops einfach nach demselben Muster ergänzen.
     * ======================================================================*/
    const SHOPS = {
        "steiff.com": {
            weiterButton: "button.continue, button[data-role='opc-continue'], button.action.primary.checkout",
            felder: {
                vorname:  "input[name='firstname']",
                nachname: "input[name='lastname']",
                email:    "input#customer-email, input[name='username']",
                telefon:  "input[name='telephone']",
                strasse:  "input[name='street[0]']",
                hausnummer: "input[name='street[1]']",
                plz:      "input[name='postcode']",
                stadt:    "input[name='city']",
                land:     "select[name='country_id']",
            },
        },
        // Generisches Magento-Muster gilt auch für viele Partner-Shops
        "mueller.de": {
            weiterButton: "",
            felder: {},
        },
    };

    // Shops, auf denen der Auto-Modus (ohne Hotkey) greift
    const AUTOMODUS_SHOPS = ["steiff.com"];

    /* ========================================================================
     * AB HIER NICHTS MEHR ÄNDERN (außer du weißt, was du tust)
     * ======================================================================*/

    const host = location.hostname.replace(/^www\./, "");
    const shopKey = Object.keys(SHOPS).find(d => host === d || host.endsWith("." + d));
    const shopConfig = (shopKey && SHOPS[shopKey]) || { felder: {} };
    const autoModusAktiv = EINSTELLUNGEN.autoModus && AUTOMODUS_SHOPS.some(d => host === d || host.endsWith("." + d));

    // Schlüsselwörter zur Feld-Erkennung (Deutsch + Englisch + Magento-Namen)
    const ERKENNUNG = {
        vorname:      ["vorname", "firstname", "first_name", "givenname", "fname"],
        nachname:     ["nachname", "lastname", "last_name", "surname", "familyname", "lname"],
        email:        ["email", "e-mail", "customer-email", "username"],
        telefon:      ["telefon", "telephone", "phone", "tel", "mobile", "handy", "mobil"],
        strasse:      ["strasse", "straße", "street", "address1", "address-line1", "adresse"],
        hausnummer:   ["hausnummer", "housenumber", "house-number", "streetnumber"],
        plz:          ["plz", "zip", "postal", "postleitzahl", "postcode", "post-code"],
        stadt:        ["stadt", "city", "ort", "town", "wohnort"],
        land:         ["land", "country", "country_id", "countryid"],
        anrede:       ["anrede", "salutation", "geschlecht", "gender", "title", "prefix"],
        geburtsdatum: ["geburtsdatum", "birthdate", "birth", "dob", "geburtstag"],
    };

    function norm(s) {
        return (s || "").toLowerCase().replace(/[\s\-_\[\]]/g, "");
    }

    function feldMerkmale(el) {
        return [
            el.name, el.id, el.placeholder,
            el.getAttribute("autocomplete"),
            el.getAttribute("aria-label"),
        ].map(norm).join("|");
    }

    // Gibt ALLE passenden sichtbaren Felder zurueck, nicht nur das erste.
    // Wichtig: Checkouts haben oft Rechnungs- UND Lieferadresse. Wer nur das
    // erste Feld fuellt, laesst die zweite Adresse leer oder trifft die falsche.
    function findeFelder(schluessel, maximal = 3) {
        const treffer = [];
        const merke = (el) => {
            if (el && el.offsetParent !== null && !treffer.includes(el)) treffer.push(el);
        };

        // 1. Shop-spezifischer Selektor hat Vorrang
        if (shopConfig.felder && shopConfig.felder[schluessel]) {
            document.querySelectorAll(shopConfig.felder[schluessel]).forEach(merke);
        }
        // 2. Automatische Erkennung über Attribut-Merkmale
        const keywords = ERKENNUNG[schluessel] || [];
        const inputs = document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), textarea");
        for (const el of inputs) {
            if (el.offsetParent === null) continue;
            const merkmale = feldMerkmale(el);
            if (keywords.some(k => merkmale.includes(norm(k)))) merke(el);
        }
        // 3. Erkennung über <label>-Text
        for (const label of document.querySelectorAll("label")) {
            const text = norm(label.textContent);
            if (!keywords.some(k => text.includes(norm(k)))) continue;
            const forId = label.getAttribute("for");
            if (forId) merke(document.getElementById(forId));
            merke(label.querySelector("input, textarea"));
        }
        return treffer.slice(0, maximal);
    }

    // Rueckwaertskompatibel: erstes Feld
    function findeFeld(schluessel) {
        return findeFelder(schluessel, 1)[0] || null;
    }

    // Wert so setzen, dass React/Vue/Knockout (Magento) es mitbekommen
    function setzeWert(el, wert) {
        el.focus();
        const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, "value");
        if (setter && setter.set) {
            setter.set.call(el, wert);
        } else {
            el.value = wert;
        }
        for (const ev of ["input", "keyup", "change", "blur"]) {
            el.dispatchEvent(new Event(ev, { bubbles: true }));
        }
    }

    function fuelleSelect(schluessel, wert) {
        const keywords = ERKENNUNG[schluessel] || [];
        // Shop-Selektor zuerst
        if (shopConfig.felder && shopConfig.felder[schluessel]) {
            const sel = document.querySelector(shopConfig.felder[schluessel]);
            if (sel && sel.tagName === "SELECT") {
                return waehleOption(sel, wert);
            }
        }
        const selects = document.querySelectorAll("select");
        for (const sel of selects) {
            if (sel.offsetParent === null) continue;
            const merkmale = feldMerkmale(sel);
            const label = sel.closest("label") || (sel.id && document.querySelector(`label[for="${sel.id}"]`));
            const labelText = norm(label ? label.textContent : "");
            if (keywords.some(k => merkmale.includes(norm(k)) || labelText.includes(norm(k)))) {
                return waehleOption(sel, wert);
            }
        }
        return false;
    }

    function waehleOption(sel, wert) {
        const option = [...sel.options].find(o =>
            norm(o.textContent).includes(norm(wert)) || norm(o.value).includes(norm(wert))
        );
        if (option) {
            sel.value = option.value;
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            highlight(sel);
            return true;
        }
        return false;
    }

    function anredeSetzen() {
        if (!PROFIL.anrede) return;
        const radios = document.querySelectorAll("input[type=radio]");
        for (const r of radios) {
            const labelEl = r.closest("label") || (r.id && document.querySelector(`label[for="${r.id}"]`));
            const umfeld = norm(r.value + " " + r.name + " " + (labelEl ? labelEl.textContent : ""));
            if (umfeld.includes(norm(PROFIL.anrede))) { r.click(); return; }
        }
        fuelleSelect("anrede", PROFIL.anrede);
    }

    function zahlartWaehlen() {
        if (!EINSTELLUNGEN.zahlart) return;
        const ziel = norm(EINSTELLUNGEN.zahlart);
        const radios = document.querySelectorAll("input[type=radio]");
        for (const r of radios) {
            const labelEl = r.closest("label") || (r.id && document.querySelector(`label[for="${r.id}"]`));
            const umfeld = norm(r.value + " " + r.name + " " + (labelEl ? labelEl.textContent : ""));
            if (umfeld.includes(ziel) && !r.checked) { r.click(); return; }
        }
    }

    function highlight(el) {
        const alt = el.style.boxShadow;
        el.style.boxShadow = "0 0 0 3px rgba(46, 204, 113, 0.8)";
        setTimeout(() => { el.style.boxShadow = alt; }, 1500);
    }

    let bereitsAusgefuellt = new WeakSet();

    function ausfuellen(still = false) {
        let gefuellt = 0;
        const felder = ["vorname", "nachname", "email", "telefon", "plz", "stadt", "geburtsdatum"];

        for (const key of felder) {
            if (!PROFIL[key]) continue;
            for (const el of findeFelder(key)) {          // Rechnung UND Lieferung
                if (bereitsAusgefuellt.has(el)) continue;
                if (!el.value) {
                    setzeWert(el, PROFIL[key]);
                    gefuellt++;
                }
                highlight(el);
                bereitsAusgefuellt.add(el);
            }
        }

        // Straße: kombiniert, wenn es nur ein Straßenfeld gibt (Magento-Standard)
        const strEl = findeFeld("strasse");
        const hnEl = findeFeld("hausnummer");
        if (strEl && !bereitsAusgefuellt.has(strEl)) {
            const wert = hnEl ? PROFIL.strasse : (PROFIL.strasse + " " + PROFIL.hausnummer).trim();
            if (!strEl.value) { setzeWert(strEl, wert); gefuellt++; }
            highlight(strEl);
            bereitsAusgefuellt.add(strEl);
        }
        if (hnEl && PROFIL.hausnummer && !bereitsAusgefuellt.has(hnEl)) {
            if (!hnEl.value) { setzeWert(hnEl, PROFIL.hausnummer); gefuellt++; }
            highlight(hnEl);
            bereitsAusgefuellt.add(hnEl);
        }

        // Land-Dropdown + Anrede
        if (PROFIL.land && fuelleSelect("land", PROFIL.land)) gefuellt++;
        anredeSetzen();

        // Zahlungsart (falls im aktuellen Schritt sichtbar)
        zahlartWaehlen();

        if (!still) zeigeMeldung(`⚡ ${gefuellt} Felder ausgefüllt`);

        if (EINSTELLUNGEN.autoWeiterKlick && gefuellt > 0) {
            const btn = findeWeiterButton();
            if (btn) setTimeout(() => btn.click(), 400);
        }
        return gefuellt;
    }

    function findeWeiterButton() {
        if (shopConfig.weiterButton) {
            const b = document.querySelector(shopConfig.weiterButton);
            if (b && b.offsetParent !== null) return b;
        }
        // Bewusst OHNE Kauf-Formulierungen: ein Auto-Klick darf niemals eine
        // Bestellung ausloesen. Kaufen bleibt immer ein Handgriff von dir.
        const kandidaten = ["weiter", "continue", "next", "zur kasse"];
        const verboten = ["kaufen", "bestellen", "zahlungspflichtig", "buy", "order", "pay"];
        const buttons = document.querySelectorAll("button, input[type=submit], a[role=button]");
        for (const b of buttons) {
            if (b.offsetParent === null) continue;
            const text = norm(b.textContent + " " + (b.value || ""));
            if (verboten.some(k => text.includes(norm(k)))) continue;   // Sicherheitsnetz
            if (kandidaten.some(k => text.includes(norm(k)))) return b;
        }
        return null;
    }

    function zeigeMeldung(text) {
        const box = document.createElement("div");
        box.textContent = text;
        Object.assign(box.style, {
            position: "fixed", bottom: "80px", right: "20px", zIndex: 999999,
            background: "#2ecc71", color: "#fff", padding: "12px 18px",
            borderRadius: "8px", fontFamily: "sans-serif", fontSize: "14px",
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
        });
        document.body.appendChild(box);
        setTimeout(() => box.remove(), 2500);
    }

    // Hotkey: Strg+Shift+1
    document.addEventListener("keydown", (e) => {
        if (e.ctrlKey && e.shiftKey && (e.code === "Digit1" || e.key === "!")) {
            e.preventDefault();
            bereitsAusgefuellt = new WeakSet(); // neu ausfüllen erlauben
            ausfuellen();
        }
    });

    // Schwebender Button
    if (EINSTELLUNGEN.zeigeButton) {
        const btn = document.createElement("button");
        btn.textContent = "⚡ Autofill";
        Object.assign(btn.style, {
            position: "fixed", bottom: "20px", right: "20px", zIndex: 999999,
            background: "#3498db", color: "#fff", border: "none",
            padding: "10px 16px", borderRadius: "8px", cursor: "pointer",
            fontFamily: "sans-serif", fontSize: "14px",
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)", opacity: "0.85",
        });
        btn.addEventListener("click", () => {
            bereitsAusgefuellt = new WeakSet();
            ausfuellen();
        });
        document.body.appendChild(btn);
    }

    // Auto-Modus: Magento-Checkout lädt Felder dynamisch nach (KnockoutJS).
    // MutationObserver füllt automatisch, sobald Adressfelder erscheinen.
    if (autoModusAktiv) {
        let timer = null;
        const observer = new MutationObserver(() => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                const hatAdressfelder = document.querySelector(
                    "input[name='firstname'], input#customer-email, input[name='postcode']"
                );
                if (hatAdressfelder) ausfuellen(true);
            }, 300);
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
})();
