document.addEventListener("DOMContentLoaded", () => {
  const configToggle = document.getElementById("config-toggle");
  const configFields = document.getElementById("config-fields");
  const saveButton = document.getElementById("cfg-save");
  const statusEl = document.getElementById("cfg-status");
  const chatPanel = document.getElementById("chat-panel");
  const chatDeckName = document.getElementById("chat-deck-name");
  const chatMessages = document.getElementById("chat-messages");
  const chatClose = document.getElementById("chat-close");
  const chatInput = document.getElementById("chat-input");
  const chatSend = document.getElementById("chat-send");
  const chatLoading = document.getElementById("chat-loading");
  const chatDeckCards = document.getElementById("chat-deck-cards");
  const hidePrecons = document.getElementById("hide-precons");
  const deckSearch = document.getElementById("deck-search");
  const deckVisibility = document.getElementById("deck-visibility");
  const deckDetailsSection = document.getElementById("deck-details");
  const deckDetailEmpty = document.getElementById("deck-detail-empty");
  const deckDetailContent = document.getElementById("deck-detail-content");
  const deckDetailName = document.getElementById("deck-detail-name");
  const deckDetailMeta = document.getElementById("deck-detail-meta");
  const deckDetailBadges = document.getElementById("deck-detail-badges");
  const deckDetailJson = document.getElementById("deck-detail-json");
  const deckDetailCards = document.getElementById("deck-detail-cards");
  const deckRows = Array.from(document.querySelectorAll(".deck-row"));

  const PILE_LABELS = [
    ["mainboard", "Mainboard"],
    ["sideboard", "Sideboard"],
    ["commandZone", "Command Zone"],
    ["companions", "Companions"],
  ];

  let selectedDeck = null;

  // --- DOM helper utilities (safe rendering via createElement/textContent) ---
  // Creates an element, applies attributes, and optionally appends children/text.
  function el(tag, opts) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.className) node.className = opts.className;
      if (opts.text != null) node.textContent = opts.text;
      if (opts.style) node.setAttribute("style", opts.style);
      if (opts.children) {
        for (const c of opts.children) {
          if (c) node.appendChild(c);
        }
      }
    }
    return node;
  }

  // Creates a <p class="meta"> with the given text.
  function metaP(text) {
    return el("p", { className: "meta", text: String(text) });
  }

  // Creates an <ul> from an array of <li> text strings or nodes.
  function ulFrom(items) {
    const list = el("ul");
    for (const item of items) {
      if (typeof item === "string") {
        list.appendChild(el("li", { text: item }));
      } else {
        list.appendChild(item);
      }
    }
    return list;
  }

  // Creates a <li> with mixed text + inline elements.
  function li(...children) {
    const item = el("li");
    for (const c of children) {
      if (typeof c === "string") {
        item.appendChild(document.createTextNode(c));
      } else {
        item.appendChild(c);
      }
    }
    return item;
  }

  // Creates a <strong> element.
  function strong(text) {
    return el("strong", { text: String(text) });
  }

  // Creates a <code> element.
  function code(text) {
    return el("code", { text: String(text) });
  }

  // Removes all children from a DOM node.
  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  // Appends all children to a node.
  function appendAll(node, children) {
    for (const c of children) {
      if (c) node.appendChild(c);
    }
  }

  // Lazy-loads card thumbnails as they scroll into view (single shared
  // observer for all card lists, restored from a WIP fix whose merge
  // conflict had discarded it in favor of a text-only rendering).
  const thumbObserver = new IntersectionObserver(
    (entries, observer) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        const placeholder = entry.target;
        const cardId = placeholder.dataset.cardId;
        if (cardId) {
          const img = document.createElement("img");
          img.className = "card-thumb";
          img.alt = placeholder.dataset.cardName || "";
          img.loading = "lazy";
          img.src = `/api/card-image/${encodeURIComponent(cardId)}`;
          img.onerror = () => {
            img.replaceWith(placeholder);
            placeholder.textContent = "?";
          };
          placeholder.replaceWith(img);
        }
        observer.unobserve(placeholder);
      }
    },
    { rootMargin: "100px" }
  );

  const addMessage = (role, text) => {
    if (!chatMessages) {
      return;
    }
    const message = document.createElement("div");
    message.className = `chat-msg ${role}`;
    message.textContent = text;
    chatMessages.appendChild(message);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  };

  const extractCards = (payload) => {
    const direct = payload && typeof payload === "object" ? payload : {};
    const cardsField = direct.cards;
    const result = {
      mainboard: [],
      sideboard: [],
      commandZone: [],
      companions: [],
    };

    if (cardsField && typeof cardsField === "object" && !Array.isArray(cardsField)) {
      for (const [pileKey] of PILE_LABELS) {
        const pile = cardsField[pileKey];
        if (Array.isArray(pile)) {
          result[pileKey] = pile;
        } else if (pile && typeof pile === "object") {
          result[pileKey] = Object.entries(pile).map(([cardId, count]) => ({
            cardId: Number.parseInt(cardId, 10) || cardId,
            name: `ID:${cardId}`,
            count,
          }));
        }
      }
    } else if (Array.isArray(cardsField)) {
      result.mainboard = cardsField;
    }

    if (!result.mainboard.length && Array.isArray(direct.mainDeck)) {
      result.mainboard = direct.mainDeck.map((card) => ({
        cardId: card.grpId ?? card.cardId ?? 0,
        name: card.name || `ID:${card.grpId ?? "?"}`,
        count: card.quantity ?? card.count ?? 1,
        rarity: card.rarity,
        set: card.set,
      }));
    }
    if (!result.sideboard.length && Array.isArray(direct.sideboard)) {
      result.sideboard = direct.sideboard.map((card) => ({
        cardId: card.grpId ?? card.cardId ?? 0,
        name: card.name || `ID:${card.grpId ?? "?"}`,
        count: card.quantity ?? card.count ?? 1,
        rarity: card.rarity,
        set: card.set,
      }));
    }
    if (!result.commandZone.length && Array.isArray(direct.commandZoneGRPIds)) {
      result.commandZone = direct.commandZoneGRPIds.map((card) => ({
        cardId: card.grpId ?? card.cardId ?? 0,
        name: card.name || `ID:${card.grpId ?? "?"}`,
        count: card.quantity ?? card.count ?? 1,
        rarity: card.rarity,
        set: card.set,
      }));
    }

    const cardsById = direct.cardsById;
    if (cardsById && typeof cardsById === "object") {
      for (const pileKey of ["mainboard", "sideboard", "commandZone", "companions"]) {
        if (!result[pileKey].length && cardsById[pileKey] && typeof cardsById[pileKey] === "object") {
          result[pileKey] = Object.entries(cardsById[pileKey]).map(([cardId, count]) => ({
            cardId,
            name: `ID:${cardId}`,
            count,
          }));
        }
      }
    }

    return result;
  };

  const renderCardSection = (title, cards) => {
    const section = document.createElement("div");
    // "deck-pile" pulls in the thumbnail/flex-row styling (.deck-pile ul,
    // .deck-pile li.with-thumb, .card-name, .qty) already defined in app.py.
    section.className = "deck-card-section deck-pile";

    const heading = document.createElement("h5");
    heading.textContent = title;
    section.appendChild(heading);

    if (!cards.length) {
      const empty = document.createElement("p");
      empty.className = "meta";
      empty.textContent = "Keine Einträge.";
      section.appendChild(empty);
      return section;
    }

    const list = document.createElement("ul");
    cards.forEach((card) => {
      const item = document.createElement("li");
      item.className = "with-thumb";
      const name = card.name || card.cardName || card.title || card.arenaId || card.cardId || "?";
      const count = card.count ?? card.quantity ?? 1;
      const extra = [];
      if (card.rarity) {
        extra.push(card.rarity);
      }
      if (card.set) {
        extra.push(card.set);
      }

      const cardId = card.cardId ?? card.grpId ?? "";
      const thumb = document.createElement("div");
      thumb.className = "card-thumb-placeholder";
      thumb.textContent = "?";
      if (cardId) {
        thumb.dataset.cardId = String(cardId);
        thumb.dataset.cardName = name;
        thumbObserver.observe(thumb);
      }

      const info = document.createElement("div");
      info.className = "card-info";

      const nameSpan = document.createElement("span");
      nameSpan.className = "card-name";
      nameSpan.textContent = extra.length ? `${name} (${extra.join(", ")})` : name;

      const qtySpan = document.createElement("span");
      qtySpan.className = "qty";
      qtySpan.textContent = `${count}x`;

      info.appendChild(nameSpan);
      info.appendChild(qtySpan);
      item.appendChild(thumb);
      item.appendChild(info);
      list.appendChild(item);
    });
    section.appendChild(list);
    return section;
  };

  const renderChatDeckCards = (payload) => {
    if (!chatDeckCards) {
      return;
    }

    chatDeckCards.replaceChildren();
    const { mainboard, sideboard, commandZone, companions } = extractCards(payload);
    const hasDeckList = mainboard.length || sideboard.length || commandZone.length || companions.length;

    if (!hasDeckList) {
      const note = document.createElement("span");
      note.className = "meta";
      note.textContent = "Keine Karten verfügbar.";
      chatDeckCards.appendChild(note);
      return;
    }

    for (const [pileKey, label] of PILE_LABELS) {
      const pile = {
        mainboard,
        sideboard,
        commandZone,
        companions,
      }[pileKey];
      if (!pile.length) {
        continue;
      }

      const pileSpan = document.createElement("div");
      pileSpan.className = "chat-deck-pile";
      const entries = pile.slice(0, 30).map((card) => {
        const name = card.name || card.cardName || card.title || card.arenaId || card.cardId || "?";
        const count = card.count ?? card.quantity ?? 1;
        return `${count}x ${name}`;
      });
      pileSpan.textContent = `${label}: ${entries.join(", ")}`;
      if (pile.length > 30) {
        pileSpan.textContent += ` (+${pile.length - 30})`;
      }
      chatDeckCards.appendChild(pileSpan);
    }
  };

  const renderDeckDetails = (payload) => {
    if (!deckDetailContent || !deckDetailEmpty) {
      return;
    }

    deckDetailEmpty.style.display = "none";
    deckDetailContent.style.display = "block";

    const name = payload.name || payload.deckName || "Unnamed";
    const deckKey = payload.deckKey || payload.deckId || "";
    const deckId = payload.deckId || "—";
    const deckTileId = payload.deckTileId ?? "—";
    const isPrecon = Boolean(payload.isPrecon);

    if (deckDetailName) {
      deckDetailName.textContent = name;
    }
    if (deckDetailMeta) {
      deckDetailMeta.textContent = `Key: ${deckKey} · DeckId: ${deckId} · DeckTileId: ${deckTileId}`;
    }
    if (deckDetailBadges) {
      deckDetailBadges.replaceChildren();
      const badges = [isPrecon ? ["Precon", "precon"] : ["Regulär", "normal"]];
      if (payload.formatLegalities) {
        badges.push(["Summary", "normal"]);
      }
      badges.forEach(([label, cls]) => {
        const badge = document.createElement("span");
        badge.className = `deck-type ${cls}`;
        badge.textContent = label;
        badge.style.marginRight = "0.35rem";
        deckDetailBadges.appendChild(badge);
      });
    }
    if (deckDetailJson) {
      deckDetailJson.textContent = JSON.stringify(payload, null, 2);
    }
    if (deckDetailCards) {
      deckDetailCards.replaceChildren();
      const { mainboard, sideboard, commandZone, companions } = extractCards(payload);
      const hasDeckList = mainboard.length || sideboard.length || commandZone.length || companions.length;

      if (!hasDeckList) {
        const note = document.createElement("p");
        note.className = "deck-detail-empty";
        note.textContent = "Diese Ansicht enthält derzeit nur eine Summary. Eine vollständige Deckliste ist für dieses Deck noch nicht verfügbar.";
        deckDetailCards.appendChild(note);
      } else {
        if (mainboard.length) {
          deckDetailCards.appendChild(renderCardSection("Mainboard", mainboard));
        }
        if (sideboard.length) {
          deckDetailCards.appendChild(renderCardSection("Sideboard", sideboard));
        }
        if (commandZone.length) {
          deckDetailCards.appendChild(renderCardSection("Command Zone", commandZone));
        }
        if (companions.length) {
          deckDetailCards.appendChild(renderCardSection("Companions", companions));
        }
      }
    }

    if (deckDetailsSection) {
      deckDetailsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  const openChatForDeck = (deckKey, deckName) => {
    selectedDeck = {
      deckKey,
      name: deckName,
    };
    if (chatDeckName) {
      chatDeckName.textContent = deckName || "Unnamed";
    }
    if (chatMessages) {
      chatMessages.replaceChildren();
    }
    if (chatPanel) {
      chatPanel.classList.add("active");
    }
    if (chatDeckCards) {
      chatDeckCards.replaceChildren();
      const loadingSpan = document.createElement("span");
      loadingSpan.className = "meta";
      loadingSpan.textContent = "Lade Karten...";
      chatDeckCards.appendChild(loadingSpan);
    }
    addMessage("assistant", `Deck "${deckName || "Unnamed"}" geladen. Frag mich was!`);
  };

  const loadChatDeckCards = async (deckKey) => {
    if (!chatDeckCards) {
      return;
    }

    try {
      const response = await fetch(`/api/deck/${encodeURIComponent(deckKey)}`);
      const payload = await response.json();
      if (!response.ok || payload.error) {
        chatDeckCards.replaceChildren();
        const note = document.createElement("span");
        note.className = "meta";
        note.textContent = payload.message || payload.error || "Karten konnten nicht geladen werden.";
        chatDeckCards.appendChild(note);
        return;
      }
      renderChatDeckCards(payload);
    } catch (error) {
      chatDeckCards.replaceChildren();
      const note = document.createElement("span");
      note.className = "meta";
      note.textContent = `Karten konnten nicht geladen werden: ${error.message}`;
      chatDeckCards.appendChild(note);
    }
  };

  const openDeckDetails = async (deckKey, deckName) => {
    if (!deckDetailEmpty || !deckDetailContent) {
      return;
    }

    deckDetailEmpty.style.display = "block";
    deckDetailEmpty.textContent = `Lade Details für ${deckName || deckKey}...`;
    deckDetailContent.style.display = "none";

    deckRows.forEach((row) => {
      row.classList.toggle("is-selected", row.dataset.deckKey === String(deckKey));
    });

    try {
      const response = await fetch(`/api/deck/${encodeURIComponent(deckKey)}`);
      const payload = await response.json();
      if (!response.ok || payload.error) {
        deckDetailEmpty.textContent = payload.message || payload.error || "Deck konnte nicht geladen werden.";
        return;
      }
      renderDeckDetails(payload);
    } catch (error) {
      deckDetailEmpty.textContent = error.message;
    }
  };

  const applyDeckFilter = () => {
    const hide = Boolean(hidePrecons?.checked);
    const query = (deckSearch?.value ?? "").trim().toLowerCase();
    let visibleCount = 0;
    let hiddenCount = 0;

    deckRows.forEach((row) => {
      const isPrecon = row.dataset.isPrecon === "true";
      const deckName = (row.dataset.deckName ?? "").toLowerCase();
      const hiddenByPrecon = hide && isPrecon;
      const hiddenBySearch = query !== "" && !deckName.includes(query);
      const shouldHide = hiddenByPrecon || hiddenBySearch;
      row.classList.toggle("hidden-by-filter", hiddenByPrecon);
      row.classList.toggle("hidden-by-search", hiddenBySearch && !hiddenByPrecon);
      if (shouldHide) {
        hiddenCount += 1;
      } else {
        visibleCount += 1;
      }
    });

    if (deckVisibility) {
      const parts = [`${visibleCount} Decks sichtbar`];
      if (hiddenCount > 0) {
        parts.push(`${hiddenCount} ausgeblendet`);
      }
      deckVisibility.textContent = parts.join(", ") + ".";
    }
  };

  if (configToggle && configFields) {
    configToggle.addEventListener("click", () => {
      configFields.classList.toggle("open");
    });
  }

  if (saveButton && statusEl) {
    saveButton.addEventListener("click", async () => {
      const payload = {
        endpoint: document.getElementById("cfg-endpoint")?.value ?? "",
        model_name: document.getElementById("cfg-model")?.value ?? "",
        temperature: Number.parseFloat(document.getElementById("cfg-temperature")?.value ?? "0"),
        max_tokens: Number.parseInt(document.getElementById("cfg-max-tokens")?.value ?? "0", 10),
      };

      statusEl.textContent = "Speichere...";
      try {
        const response = await fetch("/api/config", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        });
        if (response.ok) {
          statusEl.textContent = "✓ Gespeichert";
          statusEl.style.color = "var(--green)";
        } else {
          statusEl.textContent = "✗ Fehler";
          statusEl.style.color = "var(--danger)";
        }
      } catch (error) {
        statusEl.textContent = `✗ ${error.message}`;
        statusEl.style.color = "var(--danger)";
      }

      window.setTimeout(() => {
        statusEl.textContent = "";
      }, 3000);
    });
  }

  document.querySelectorAll(".btn-select-deck").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const deckKey = button.dataset.deckKey || "";
      const deckName = button.dataset.deckName || "Unnamed";
      openChatForDeck(deckKey, deckName);
      await loadChatDeckCards(deckKey);
    });
  });

  deckRows.forEach((row) => {
    row.addEventListener("click", () => {
      openDeckDetails(row.dataset.deckKey || "", row.dataset.deckName || "Unnamed");
    });
  });

  if (hidePrecons) {
    hidePrecons.addEventListener("change", applyDeckFilter);
  }

  if (deckSearch) {
    deckSearch.addEventListener("input", applyDeckFilter);
  }

  if (chatClose && chatPanel) {
    chatClose.addEventListener("click", () => {
      chatPanel.classList.remove("active");
    });
  }

  const sendChat = async () => {
    const question = chatInput?.value.trim() ?? "";
    if (!question || !selectedDeck) {
      return;
    }

    if (chatInput) {
      chatInput.value = "";
    }
    if (chatSend) {
      chatSend.disabled = true;
    }
    if (chatLoading) {
      chatLoading.style.display = "block";
    }
    addMessage("user", question);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          deck_id: selectedDeck.deckKey,
          question,
        }),
      });
      const payload = await response.json();
      if (payload.error) {
        addMessage("error", payload.error);
      } else {
        addMessage("assistant", payload.response || "");
      }
    } catch (error) {
      addMessage("error", error.message);
    }

    if (chatSend) {
      chatSend.disabled = false;
    }
    if (chatLoading) {
      chatLoading.style.display = "none";
    }
  };

  if (chatSend) {
    chatSend.addEventListener("click", sendChat);
  }
  if (chatInput) {
    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        sendChat();
      }
    });
  }

  applyDeckFilter();
  // --- History controls ---
  const historySaveBtn = document.getElementById("history-save-btn");
  const historyDiffBtn = document.getElementById("history-diff-btn");
  const historyDiffResult = document.getElementById("history-diff-result");
  const historyDiffContent = document.getElementById("history-diff-content");

  if (historySaveBtn) {
    historySaveBtn.addEventListener("click", async () => {
      historySaveBtn.disabled = true;
      historySaveBtn.textContent = "Speichere...";
      try {
        const resp = await fetch("/api/history/save", { method: "POST" });
        await resp.json().catch(() => ({}));
        if (resp.ok) {
          historySaveBtn.textContent = "✓ Gespeichert";
          window.setTimeout(() => location.reload(), 800);
        } else {
          historySaveBtn.textContent = "✗ Fehler";
          historySaveBtn.disabled = false;
        }
      } catch (err) {
        historySaveBtn.textContent = `✗ ${err.message}`;
        historySaveBtn.disabled = false;
      }
    });
  }

  if (historyDiffBtn && historyDiffResult && historyDiffContent) {
    historyDiffBtn.addEventListener("click", async () => {
      historyDiffBtn.disabled = true;
      historyDiffBtn.textContent = "Lade Diff...";
      historyDiffResult.style.display = "block";
      clearChildren(historyDiffContent);
      try {
        const resp = await fetch("/api/history/diff");
        const data = await resp.json();
        if (data.error) {
          historyDiffContent.appendChild(metaP(data.error));
        } else {
          const fragment = document.createDocumentFragment();
          const cd = data.collectionDiff;
          if (cd && !cd.error) {
            const s = cd.summary;
            fragment.appendChild(el("h4", { text: "Collection" }));
            fragment.appendChild(metaP(
              "+" + s.added + " neu, +" + s.increased + " erhöht, " +
              "-" + s.removed + " entfernt, -" + s.decreased + " reduziert, " +
              s.unchanged + " unverändert"
            ));
            fragment.appendChild(metaP(
              "Netto: " + (s.netChange >= 0 ? "+" : "") + s.netChange + " Karten"
            ));
            if (cd.added && cd.added.length > 0) {
              fragment.appendChild(ulFrom(
                cd.added.slice(0, 20).map(c => "+" + c.count + "x Card " + c.cardId + " (neu)")
              ));
            }
            if (cd.increased && cd.increased.length > 0) {
              fragment.appendChild(ulFrom(
                cd.increased.slice(0, 20).map(c =>
                  "+" + c.delta + "x Card " + c.cardId + " (jetzt " + c.newCount + "x, war " + c.oldCount + "x)"
                )
              ));
            }
            if (cd.removed && cd.removed.length > 0) {
              fragment.appendChild(ulFrom(
                cd.removed.slice(0, 20).map(c => "-" + c.oldCount + "x Card " + c.cardId + " (entfernt)")
              ));
            }
            if (cd.decreased && cd.decreased.length > 0) {
              fragment.appendChild(ulFrom(
                cd.decreased.slice(0, 20).map(c =>
                  "-" + c.delta + "x Card " + c.cardId + " (jetzt " + c.newCount + "x, war " + c.oldCount + "x)"
                )
              ));
            }
            if (cd.wildcardDiff) {
              fragment.appendChild(el("h5", { text: "Wildcards" }));
              const wcItems = [];
              for (const [key, change] of Object.entries(cd.wildcardDiff)) {
                wcItems.push(key + ": " + change.old + " → " + change.new + " (" + (change.delta >= 0 ? "+" : "") + change.delta + ")");
              }
              fragment.appendChild(ulFrom(wcItems));
            }
          }

          const dd = data.decksDiff;
          if (dd) {
            const s = dd.summary;
            fragment.appendChild(el("h4", { text: "Decks" }));
            fragment.appendChild(metaP("+" + s.added + " neu, -" + s.removed + " entfernt, ~" + s.modified + " verändert"));
            if (dd.added && dd.added.length > 0) {
              fragment.appendChild(ulFrom(dd.added.map(d => "+ " + d.name + " (" + d.deckId + ")")));
            }
            if (dd.removed && dd.removed.length > 0) {
              fragment.appendChild(ulFrom(dd.removed.map(d => "- " + d.name + " (" + d.deckId + ")")));
            }
            if (dd.modified && dd.modified.length > 0) {
              fragment.appendChild(ulFrom(dd.modified.map(d => "~ " + d.name + " (" + d.deckId + ")")));
            }
          }

          if (!fragment.hasChildNodes()) {
            fragment.appendChild(metaP("Keine Änderungen."));
          }
          historyDiffContent.appendChild(fragment);
        }
      } catch (err) {
        clearChildren(historyDiffContent);
        historyDiffContent.appendChild(metaP("Fehler: " + err.message));
      }
      historyDiffBtn.disabled = false;
      historyDiffBtn.textContent = "Diff anzeigen";
    });
  }

  // --- Ranks & Account section ---
  const ranksContent = document.getElementById("ranks-content");
  if (ranksContent) {
    fetch("/api/ranks")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        const ranks = data.ranks || {};
        const account = data.account || {};
        const constructed = ranks.constructed || {};
        const limited = ranks.limited || {};
        const warnings = data.warnings || [];

        clearChildren(ranksContent);
        const fragment = document.createDocumentFragment();

        // Two-column grid for Constructed / Limited
        const grid = el("div", {
          style: "display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;",
        });

        function rankColumn(title, rank) {
          const col = el("div", {
            style: "padding:1rem;border:1px solid var(--border);border-radius:8px;",
          });
          col.appendChild(el("h3", { style: "margin:0 0 .5rem;", text: title }));
          if (rank.class && rank.class !== "None") {
            col.appendChild(el("p", {
              style: "font-size:1.4rem;font-weight:bold;margin:.25rem 0;",
              text: rank.class,
            }));
            if (rank.level) {
              col.appendChild(metaP("Level " + rank.level + ", Step " + (rank.step || 0)));
            }
            const seasonText = "Saison " + (rank.seasonOrdinal || "?") + ": " +
              (rank.wins || 0) + "-" + (rank.losses || 0) +
              (rank.draws ? "-" + rank.draws : "");
            col.appendChild(metaP(seasonText));
            if (rank.leaderboardPlace) {
              col.appendChild(metaP("Leaderboard #" + rank.leaderboardPlace));
            }
            if (rank.percentile) {
              col.appendChild(metaP("Percentile: " + rank.percentile));
            }
          } else {
            col.appendChild(metaP("Unranked"));
          }
          return col;
        }

        grid.appendChild(rankColumn("Constructed", constructed));
        grid.appendChild(rankColumn("Limited", limited));
        fragment.appendChild(grid);

        // Account section
        if (account.displayName || account.accountId) {
          const acctDiv = el("div", {
            style: "padding:1rem;border:1px solid var(--border);border-radius:8px;margin-bottom:1rem;",
          });
          acctDiv.appendChild(el("h3", { style: "margin:0 0 .5rem;", text: "Account" }));
          if (account.displayName) {
            const p = metaP("Name: ");
            p.appendChild(strong(account.displayName));
            acctDiv.appendChild(p);
          }
          if (account.countryCode) acctDiv.appendChild(metaP("Land: " + account.countryCode));
          if (account.accountId) acctDiv.appendChild(metaP("Account ID: " + account.accountId));
          if (account.personaId) acctDiv.appendChild(metaP("Persona ID: " + account.personaId));
          if (account.gameId) acctDiv.appendChild(metaP("Game ID: " + account.gameId));
          fragment.appendChild(acctDiv);
        }

        if (ranks.playerId) {
          fragment.appendChild(metaP("Player ID: " + ranks.playerId));
        }

        if (warnings.length > 0) {
          const warnDiv = el("div", { className: "warning" });
          warnDiv.appendChild(el("h3", { text: "Warnings" }));
          warnDiv.appendChild(ulFrom(warnings));
          fragment.appendChild(warnDiv);
        }

        if (!fragment.hasChildNodes()) {
          fragment.appendChild(metaP("Keine Rang-Daten verfügbar."));
        }
        ranksContent.appendChild(fragment);
      })
      .catch((err) => {
        clearChildren(ranksContent);
        const p = metaP("Rang-Daten nicht verfügbar — " + err.message + ". Führe ");
        p.appendChild(code("mtga-export ranks --output out/ranks.json"));
        p.appendChild(document.createTextNode(" aus."));
        ranksContent.appendChild(p);
      });
  }

  // --- Meta-Daten section (MTGGoldfish) ---
  const metaContent = document.getElementById("meta-content");
  if (metaContent) {
    fetch("/api/meta?format=standard")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        const topDecks = data.topDecks || [];
        const topCards = data.topCards || [];
        const warnings = data.warnings || [];
        const fmt = (data.format || "standard").charAt(0).toUpperCase() + (data.format || "standard").slice(1);
        const fetchedAt = data.fetchedAt || "?";

        clearChildren(metaContent);
        const fragment = document.createDocumentFragment();

        // Format info line: "Format: **Standard** · Quelle: MTGGoldfish · Abgerufen: ..."
        const fmtP = metaP("Format: ");
        fmtP.appendChild(strong(fmt));
        fmtP.appendChild(document.createTextNode(" · Quelle: MTGGoldfish · Abgerufen: " + fetchedAt));
        fragment.appendChild(fmtP);

        if (warnings.length > 0) {
          const warnDiv = el("div", { className: "warning" });
          warnDiv.appendChild(el("h3", { text: "Warnings" }));
          warnDiv.appendChild(ulFrom(warnings));
          fragment.appendChild(warnDiv);
        }

        if (topDecks.length > 0) {
          fragment.appendChild(el("h3", { text: "Top-Decks" }));
          const table = el("table", { style: "width:100%;border-collapse:collapse;" });

          // thead
          const thead = el("thead");
          const headRow = el("tr", { style: "text-align:left;border-bottom:1px solid var(--border);" });
          for (const hdr of ["#", "Deck", "Meta%", "Decks", "Top-Karten"]) {
            headRow.appendChild(el("th", { style: "padding:.3rem;", text: hdr }));
          }
          thead.appendChild(headRow);
          table.appendChild(thead);

          // tbody
          const tbody = el("tbody");
          topDecks.forEach((deck, i) => {
            const cards = (deck.topCards || []).slice(0, 3).join(", ");
            const row = el("tr", { style: "border-bottom:1px solid var(--border);" });

            row.appendChild(el("td", { style: "padding:.3rem;", text: String(i + 1) }));

            const deckTd = el("td", { style: "padding:.3rem;" });
            deckTd.appendChild(strong(deck.name));
            if (deck.colors) {
              deckTd.appendChild(document.createTextNode(" "));
              deckTd.appendChild(el("span", {
                style: "font-size:.8rem;color:var(--text-dim);",
                text: "(" + deck.colors + ")",
              }));
            }
            row.appendChild(deckTd);

            row.appendChild(el("td", { style: "padding:.3rem;", text: deck.metaShare.toFixed(1) + "%" }));
            row.appendChild(el("td", { style: "padding:.3rem;", text: String(deck.deckCount) }));
            row.appendChild(el("td", { style: "padding:.3rem;font-size:.85rem;", text: cards }));

            tbody.appendChild(row);
          });
          table.appendChild(tbody);
          fragment.appendChild(table);
        }

        if (topCards.length > 0) {
          fragment.appendChild(el("h3", { text: "Häufigste Karten" }));
          const grid = el("div", {
            style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.5rem;",
          });
          for (const card of topCards.slice(0, 15)) {
            const cardDiv = el("div", {
              style: "padding:.5rem;border:1px solid var(--border);border-radius:6px;",
            });
            cardDiv.appendChild(strong(card.name));
            cardDiv.appendChild(el("br"));
            cardDiv.appendChild(el("span", { className: "meta", text: card.decks + " Decks" }));
            grid.appendChild(cardDiv);
          }
          fragment.appendChild(grid);
        }

        if (!fragment.hasChildNodes()) {
          fragment.appendChild(metaP("Keine Meta-Daten verfügbar."));
        }
        metaContent.appendChild(fragment);
      })
      .catch((err) => {
        clearChildren(metaContent);
        metaContent.appendChild(metaP("Meta-Daten nicht verfügbar — " + err.message + "."));
      });
  }

  // --- New Deck Builder Form ---
  const builderSubmit = document.getElementById("builder-submit");
  const builderFormat = document.getElementById("builder-format");
  const builderColorsContainer = document.getElementById("builder-colors");
  const builderArchetype = document.getElementById("builder-archetype");
  const builderMaxRares = document.getElementById("builder-max-rares");
  const builderMaxMythics = document.getElementById("builder-max-mythics");
  const builderBudget = document.getElementById("builder-budget");
  const builderUseMeta = document.getElementById("builder-use-meta");
  const builderStatus = document.getElementById("builder-status");
  const builderResult = document.getElementById("builder-result");

  function builderSetStatus(text, isError) {
    if (!builderStatus) {
      return;
    }
    builderStatus.textContent = text;
    builderStatus.className = "meta" + (isError ? " error" : (text ? " success" : ""));
  }

  function renderBuilderResult(data) {
    if (!builderResult) {
      return;
    }
    clearChildren(builderResult);

    // Summary block
    const summaryBlock = el("div", { className: "builder-result-block" });
    summaryBlock.appendChild(el("h4", { text: "Summary" }));
    const summary = data.summary || {};
    if (summary.deckConcept) {
      summaryBlock.appendChild(el("p", { className: "meta", text: "Concept: " + summary.deckConcept }));
    }
    if (summary.confidence) {
      summaryBlock.appendChild(el("p", { className: "meta", text: "Confidence: " + summary.confidence }));
    }
    if (Array.isArray(summary.constraints) && summary.constraints.length) {
      summaryBlock.appendChild(el("p", { className: "meta", text: "Constraints: " + summary.constraints.join(", ") }));
    }
    if (summary.note) {
      summaryBlock.appendChild(el("p", { className: "meta", text: summary.note }));
    }
    builderResult.appendChild(summaryBlock);

    // Deck draft block
    const draft = data.deckDraft || {};
    const draftBlock = el("div", { className: "builder-result-block" });
    draftBlock.appendChild(el("h4", { text: "Deck Draft" }));
    const piles = [["mainboard", "Mainboard"], ["sideboard", "Sideboard"], ["commandZone", "Command Zone"]];
    let hasCards = false;
    for (const [key, label] of piles) {
      const cards = draft[key];
      if (Array.isArray(cards) && cards.length) {
        hasCards = true;
        draftBlock.appendChild(renderCardSection(label, cards));
      }
    }
    if (!hasCards) {
      draftBlock.appendChild(metaP("No cards in draft yet (stub mode)."));
    }
    builderResult.appendChild(draftBlock);

    // Suggestions block
    if (Array.isArray(data.suggestions) && data.suggestions.length) {
      const sugBlock = el("div", { className: "builder-result-block" });
      sugBlock.appendChild(el("h4", { text: "Suggestions" }));
      const items = data.suggestions.map((s) => {
        if (typeof s === "string") {
          return s;
        }
        return s.text || s.description || JSON.stringify(s);
      });
      sugBlock.appendChild(ulFrom(items));
      builderResult.appendChild(sugBlock);
    }

    // Warnings block
    if (Array.isArray(data.warnings) && data.warnings.length) {
      const warnBlock = el("div", { className: "builder-result-block" });
      warnBlock.appendChild(el("h4", { text: "Warnings" }));
      warnBlock.appendChild(ulFrom(data.warnings));
      builderResult.appendChild(warnBlock);
    }

    // Schema badge
    if (data.schema) {
      builderResult.appendChild(el("p", { className: "meta", text: "Schema: " + data.schema }));
    }
  }

  if (builderSubmit) {
    builderSubmit.addEventListener("click", () => {
      if (!builderFormat || !builderFormat.value) {
        builderSetStatus("Bitte Format wählen.", true);
        return;
      }

      // Collect checked colors
      const colors = [];
      if (builderColorsContainer) {
        const checkboxes = builderColorsContainer.querySelectorAll('input[type="checkbox"]:checked');
        for (const cb of checkboxes) {
          colors.push(cb.value);
        }
      }

      // Build request body
      const requestBody = {
        format: builderFormat.value,
        colors: colors,
        archetype: builderArchetype ? builderArchetype.value.trim() : "",
        maxRares: builderMaxRares ? parseInt(builderMaxRares.value, 10) : undefined,
        budgetMode: builderBudget ? builderBudget.value : "owned-first",
        useMeta: builderUseMeta ? builderUseMeta.checked : true,
      };

      // Optional maxMythics — only send if filled
      if (builderMaxMythics && builderMaxMythics.value.trim()) {
        const mm = parseInt(builderMaxMythics.value, 10);
        if (!isNaN(mm)) {
          requestBody.maxMythics = mm;
        }
      }

      // Remove undefined fields
      if (requestBody.maxRares === undefined || isNaN(requestBody.maxRares)) {
        delete requestBody.maxRares;
      }
      if (!requestBody.archetype) {
        delete requestBody.archetype;
      }

      builderSetStatus("Sende Anfrage...", false);
      builderSubmit.disabled = true;

      fetch("/api/advisor/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      })
        .then((resp) => resp.json().then((data) => ({ status: resp.status, data })))
        .then(({ status, data }) => {
          builderSubmit.disabled = false;
          if (status !== 200) {
            const errMsg = data.message || data.error || "Unknown error";
            builderSetStatus("Fehler: " + errMsg, true);
            return;
          }
          builderSetStatus("Entwurf erhalten.", false);
          renderBuilderResult(data);
        })
        .catch((err) => {
          builderSubmit.disabled = false;
          builderSetStatus("Netzwerkfehler: " + err.message, true);
        });
    });
  }
});
