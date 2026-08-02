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
    section.className = "deck-card-section";

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
    list.className = "deck-list";
    cards.forEach((card) => {
      const item = document.createElement("li");
      const name = card.name || card.cardName || card.title || card.arenaId || card.cardId || "?";
      const count = card.count ?? card.quantity ?? 1;
      const extra = [];
      if (card.rarity) {
        extra.push(card.rarity);
      }
      if (card.set) {
        extra.push(card.set);
      }
      item.textContent = extra.length ? `${name} x${count} (${extra.join(", ")})` : `${name} x${count}`;
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
      const response = await fetch(`/api/decks/${encodeURIComponent(deckKey)}`);
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
      const response = await fetch(`/api/decks/${encodeURIComponent(deckKey)}`);
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
    let visibleCount = 0;
    let hiddenCount = 0;

    deckRows.forEach((row) => {
      const isPrecon = row.dataset.isPrecon === "true";
      const shouldHide = hide && isPrecon;
      row.classList.toggle("hidden-by-filter", shouldHide);
      if (shouldHide) {
        hiddenCount += 1;
      } else {
        visibleCount += 1;
      }
    });

    if (deckVisibility) {
      deckVisibility.textContent = hide
        ? `${visibleCount} Decks sichtbar, ${hiddenCount} Precons ausgeblendet.`
        : `${visibleCount} Decks sichtbar.`;
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
});
