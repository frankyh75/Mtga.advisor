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
      try {
        const resp = await fetch("/api/history/diff");
        const data = await resp.json();
        if (data.error) {
          historyDiffContent.innerHTML = `<p class="meta">${data.error}</p>`;
        } else {
          let html = "";
          const cd = data.collectionDiff;
          if (cd && !cd.error) {
            const s = cd.summary;
            html += `<h4>Collection</h4>`;
            html += `<p class="meta">+${s.added} neu, +${s.increased} erhöht, -${s.removed} entfernt, -${s.decreased} reduziert, ${s.unchanged} unverändert</p>`;
            html += `<p class="meta">Netto: ${s.netChange >= 0 ? "+" : ""}${s.netChange} Karten</p>`;
            if (cd.added && cd.added.length > 0) {
              html += "<ul>";
              for (const c of cd.added.slice(0, 20)) {
                html += `<li>+${c.count}x Card ${c.cardId} (neu)</li>`;
              }
              html += "</ul>";
            }
            if (cd.increased && cd.increased.length > 0) {
              html += "<ul>";
              for (const c of cd.increased.slice(0, 20)) {
                html += `<li>+${c.delta}x Card ${c.cardId} (jetzt ${c.newCount}x, war ${c.oldCount}x)</li>`;
              }
              html += "</ul>";
            }
            if (cd.removed && cd.removed.length > 0) {
              html += "<ul>";
              for (const c of cd.removed.slice(0, 20)) {
                html += `<li>-${c.oldCount}x Card ${c.cardId} (entfernt)</li>`;
              }
              html += "</ul>";
            }
            if (cd.decreased && cd.decreased.length > 0) {
              html += "<ul>";
              for (const c of cd.decreased.slice(0, 20)) {
                html += `<li>-${c.delta}x Card ${c.cardId} (jetzt ${c.newCount}x, war ${c.oldCount}x)</li>`;
              }
              html += "</ul>";
            }
            if (cd.wildcardDiff) {
              html += "<h5>Wildcards</h5><ul>";
              for (const [key, change] of Object.entries(cd.wildcardDiff)) {
                html += `<li>${key}: ${change.old} → ${change.new} (${change.delta >= 0 ? "+" : ""}${change.delta})</li>`;
              }
              html += "</ul>";
            }
          }

          const dd = data.decksDiff;
          if (dd) {
            const s = dd.summary;
            html += `<h4>Decks</h4>`;
            html += `<p class="meta">+${s.added} neu, -${s.removed} entfernt, ~${s.modified} verändert</p>`;
            if (dd.added && dd.added.length > 0) {
              html += "<ul>";
              for (const d of dd.added) {
                html += `<li>+ ${d.name} (${d.deckId})</li>`;
              }
              html += "</ul>";
            }
            if (dd.removed && dd.removed.length > 0) {
              html += "<ul>";
              for (const d of dd.removed) {
                html += `<li>- ${d.name} (${d.deckId})</li>`;
              }
              html += "</ul>";
            }
            if (dd.modified && dd.modified.length > 0) {
              html += "<ul>";
              for (const d of dd.modified) {
                html += `<li>~ ${d.name} (${d.deckId})</li>`;
              }
              html += "</ul>";
            }
          }

          historyDiffContent.innerHTML = html || "<p class='meta'>Keine Änderungen.</p>";
        }
      } catch (err) {
        historyDiffContent.innerHTML = `<p class="meta">Fehler: ${err.message}</p>`;
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

        let html = "";

        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;">';
        html += '<div style="padding:1rem;border:1px solid var(--border);border-radius:8px;">';
        html += '<h3 style="margin:0 0 .5rem;">Constructed</h3>';
        if (constructed.class && constructed.class !== "None") {
          html += `<p style="font-size:1.4rem;font-weight:bold;margin:.25rem 0;">${constructed.class}</p>`;
          if (constructed.level) html += `<p class="meta">Level ${constructed.level}, Step ${constructed.step || 0}</p>`;
          html += `<p class="meta">Saison ${constructed.seasonOrdinal || "?"}: ${constructed.wins || 0}-${constructed.losses || 0}${constructed.draws ? `-${constructed.draws}` : ""}</p>`;
          if (constructed.leaderboardPlace) html += `<p class="meta">Leaderboard #${constructed.leaderboardPlace}</p>`;
          if (constructed.percentile) html += `<p class="meta">Percentile: ${constructed.percentile}</p>`;
        } else {
          html += '<p class="meta">Unranked</p>';
        }
        html += "</div>";

        html += '<div style="padding:1rem;border:1px solid var(--border);border-radius:8px;">';
        html += '<h3 style="margin:0 0 .5rem;">Limited</h3>';
        if (limited.class && limited.class !== "None") {
          html += `<p style="font-size:1.4rem;font-weight:bold;margin:.25rem 0;">${limited.class}</p>`;
          if (limited.level) html += `<p class="meta">Level ${limited.level}, Step ${limited.step || 0}</p>`;
          html += `<p class="meta">Saison ${limited.seasonOrdinal || "?"}: ${limited.wins || 0}-${limited.losses || 0}${limited.draws ? `-${limited.draws}` : ""}</p>`;
          if (limited.leaderboardPlace) html += `<p class="meta">Leaderboard #${limited.leaderboardPlace}</p>`;
          if (limited.percentile) html += `<p class="meta">Percentile: ${limited.percentile}</p>`;
        } else {
          html += '<p class="meta">Unranked</p>';
        }
        html += "</div>";
        html += "</div>";

        if (account.displayName || account.accountId) {
          html += '<div style="padding:1rem;border:1px solid var(--border);border-radius:8px;margin-bottom:1rem;">';
          html += '<h3 style="margin:0 0 .5rem;">Account</h3>';
          if (account.displayName) html += `<p class="meta">Name: <strong>${account.displayName}</strong></p>`;
          if (account.countryCode) html += `<p class="meta">Land: ${account.countryCode}</p>`;
          if (account.accountId) html += `<p class="meta">Account ID: ${account.accountId}</p>`;
          if (account.personaId) html += `<p class="meta">Persona ID: ${account.personaId}</p>`;
          if (account.gameId) html += `<p class="meta">Game ID: ${account.gameId}</p>`;
          html += "</div>";
        }

        if (ranks.playerId) {
          html += `<p class="meta">Player ID: ${ranks.playerId}</p>`;
        }

        if (warnings.length > 0) {
          html += '<div class="warning"><h3>Warnings</h3><ul>';
          for (const w of warnings) {
            html += `<li>${w}</li>`;
          }
          html += "</ul></div>";
        }

        if (!html) {
          html = '<p class="meta">Keine Rang-Daten verfügbar.</p>';
        }

        ranksContent.innerHTML = html;
      })
      .catch((err) => {
        ranksContent.innerHTML = `<p class="meta">Rang-Daten nicht verfügbar — ${err.message}. Führe <code>mtga-export ranks --output out/ranks.json</code> aus.</p>`;
      });
  }

  // --- Helper-Status section ---
  const helperContent = document.getElementById("helper-status-content");
  if (helperContent) {
    const renderHelperStatus = (data) => {
      const running = data.running;
      const installed = data.installed;
      const bundleBuilt = data.bundle_built;

      let indicatorClass = "unknown";
      let statusLabel = "Unbekannt";
      let statusDetail = "";

      if (running) {
        indicatorClass = "running";
        statusLabel = "Läuft";
        statusDetail = `PID: ${data.pid ?? "?"} · Version: ${data.version ?? "?"} · Socket: ${data.socket_path}`;
      } else if (installed) {
        indicatorClass = "installed";
        statusLabel = "Installiert (läuft nicht)";
        statusDetail = "Daemon installiert aber nicht aktiv. Neu starten: sudo launchctl load /Library/LaunchDaemons/com.mtga.helper.plist";
      } else if (bundleBuilt) {
        indicatorClass = "not-installed";
        statusLabel = "Gebaut, nicht installiert";
        statusDetail = data.install_instructions || "sudo ./helper/install.sh";
      } else {
        indicatorClass = "not-installed";
        statusLabel = "Nicht installiert";
        statusDetail = data.install_instructions || "Helper bauen und installieren: make -C helper && sudo ./helper/install.sh";
      }

      let html = '<div class="helper-status-box">';
      html += `<div class="helper-indicator ${indicatorClass}"></div>`;
      html += '<div class="helper-status-text">';
      html += `<strong>${statusLabel}</strong>`;
      html += `<p class="meta">${statusDetail}</p>`;
      html += '</div>';

      if (!installed && bundleBuilt) {
        html += '<button class="helper-btn" id="helper-install-btn">Helper installieren</button>';
      } else if (!installed && !bundleBuilt) {
        html += '<button class="helper-btn" id="helper-build-btn" disabled>Bundle fehlt — make -C helper</button>';
      } else if (installed && !running) {
        html += '<button class="helper-btn btn-green" id="helper-start-btn">Daemon starten</button>';
      } else if (running) {
        html += '<button class="helper-btn" id="helper-refresh-btn">Aktualisieren</button>';
      }

      html += '</div>';

      // Detail-Liste
      html += '<ul class="helper-detail-list">';
      html += `<li>Bundle gebaut: ${bundleBuilt ? "ja" : "nein"}</li>`;
      html += `<li>Installiert: ${installed ? "ja" : "nein"}</li>`;
      html += `<li>Daemon läuft: ${running ? "ja" : "nein"}</li>`;
      html += `<li>Socket: ${data.socket_path}</li>`;
      html += '</ul>';

      helperContent.innerHTML = html;

      // Install-Button Handler
      const installBtn = document.getElementById("helper-install-btn");
      if (installBtn) {
        installBtn.addEventListener("click", async () => {
          installBtn.disabled = true;
          installBtn.textContent = "Öffne Terminal...";
          // Wir können sudo nicht direkt aus dem Browser ausführen.
          // Zeige eine Anleitung mit dem genauen Befehl.
          helperContent.insertAdjacentHTML("beforeend",
            '<div class="helper-status-box" style="margin-top:.5rem;border-color:var(--accent);">' +
            '<div class="helper-status-text">' +
            '<strong>Installation im Terminal</strong>' +
            '<p class="meta">Der Browser kann keine root-Rechte erlangen. Bitte im Terminal ausführen:</p>' +
            '<pre style="margin:.5rem 0;font-size:.85rem;">cd ' + window.location.pathname.replace("/","") + "\n" + "sudo ./helper/install.sh</pre>" +
            '<p class="meta">Nach der Installation: Status aktualisieren.</p>' +
            '</div><button class="helper-btn btn-green" id="helper-refresh-after-install">Status aktualisieren</button>' +
            '</div>'
          );
          installBtn.remove();

          const refreshBtn = document.getElementById("helper-refresh-after-install");
          if (refreshBtn) {
            refreshBtn.addEventListener("click", () => loadHelperStatus());
          }
        });
      }

      // Start-Button Handler (Daemon läuft nicht, aber ist installiert)
      const startBtn = document.getElementById("helper-start-btn");
      if (startBtn) {
        startBtn.addEventListener("click", async () => {
          startBtn.disabled = true;
          startBtn.textContent = "Öffne Terminal...";
          helperContent.insertAdjacentHTML("beforeend",
            '<div class="helper-status-box" style="margin-top:.5rem;border-color:var(--accent);">' +
            '<div class="helper-status-text">' +
            '<strong>Daemon starten</strong>' +
            '<p class="meta">Im Terminal ausführen:</p>' +
            '<pre style="margin:.5rem 0;font-size:.85rem;">sudo launchctl load /Library/LaunchDaemons/com.mtga.helper.plist</pre>' +
            '</div><button class="helper-btn btn-green" id="helper-refresh-after-start">Status aktualisieren</button>' +
            '</div>'
          );
          startBtn.remove();

          const refreshBtn = document.getElementById("helper-refresh-after-start");
          if (refreshBtn) {
            refreshBtn.addEventListener("click", () => loadHelperStatus());
          }
        });
      }

      // Refresh-Button Handler
      const refreshBtn = document.getElementById("helper-refresh-btn");
      if (refreshBtn) {
        refreshBtn.addEventListener("click", () => loadHelperStatus());
      }
    };

    const loadHelperStatus = async () => {
      try {
        const resp = await fetch("/api/helper/status");
        const data = await resp.json();
        renderHelperStatus(data);
      } catch (err) {
        helperContent.innerHTML = `<p class="meta">Helper-Status nicht abrufbar: ${err.message}</p>`;
      }
    };

    loadHelperStatus();
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

        let html = "";
        html += `<p class="meta">Format: <strong>${fmt}</strong> · Quelle: MTGGoldfish · Abgerufen: ${fetchedAt}</p>`;

        if (warnings.length > 0) {
          html += '<div class="warning"><h3>Warnings</h3><ul>';
          for (const w of warnings) {
            html += `<li>${w}</li>`;
          }
          html += "</ul></div>";
        }

        if (topDecks.length > 0) {
          html += '<h3>Top-Decks</h3>';
          html += '<table style="width:100%;border-collapse:collapse;">';
          html += '<thead><tr style="text-align:left;border-bottom:1px solid var(--border);">';
          html += '<th style="padding:.3rem;">#</th>';
          html += '<th style="padding:.3rem;">Deck</th>';
          html += '<th style="padding:.3rem;">Meta%</th>';
          html += '<th style="padding:.3rem;">Decks</th>';
          html += '<th style="padding:.3rem;">Top-Karten</th>';
          html += "</tr></thead><tbody>";
          topDecks.forEach((deck, i) => {
            const cards = (deck.topCards || []).slice(0, 3).join(", ");
            const colors = deck.colors ? ` <span style="font-size:.8rem;color:var(--text-dim);">(${deck.colors})</span>` : "";
            html += '<tr style="border-bottom:1px solid var(--border);">';
            html += `<td style="padding:.3rem;">${i + 1}</td>`;
            html += `<td style="padding:.3rem;"><strong>${deck.name}</strong>${colors}</td>`;
            html += `<td style="padding:.3rem;">${deck.metaShare.toFixed(1)}%</td>`;
            html += `<td style="padding:.3rem;">${deck.deckCount}</td>`;
            html += `<td style="padding:.3rem;font-size:.85rem;">${cards}</td>`;
            html += "</tr>";
          });
          html += "</tbody></table>";
        }

        if (topCards.length > 0) {
          html += '<h3>Häufigste Karten</h3>';
          html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.5rem;">';
          for (const card of topCards.slice(0, 15)) {
            html += `<div style="padding:.5rem;border:1px solid var(--border);border-radius:6px;">`;
            html += `<strong>${card.name}</strong><br><span class="meta">${card.decks} Decks</span>`;
            html += "</div>";
          }
          html += "</div>";
        }

        if (!html) {
          html = '<p class="meta">Keine Meta-Daten verfügbar.</p>';
        }

        metaContent.innerHTML = html;
      })
      .catch((err) => {
        metaContent.innerHTML = `<p class="meta">Meta-Daten nicht verfügbar — ${err.message}.</p>`;
      });
  }
});
