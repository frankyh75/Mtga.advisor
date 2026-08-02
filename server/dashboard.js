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

  // Deck detail panel elements
  const deckDetail = document.getElementById("deck-detail");
  const deckDetailName = document.getElementById("deck-detail-name");
  const deckDetailMeta = document.getElementById("deck-detail-meta");
  const deckCardsGrid = document.getElementById("deck-cards-grid");
  const deckDetailClose = document.getElementById("deck-detail-close");

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

  const PILE_LABELS = [
    ["mainboard", "Mainboard"],
    ["sideboard", "Sideboard"],
    ["commandZone", "Command Zone"],
    ["companions", "Companions"],
  ];

  const renderDeckCards = (deckData) => {
    if (!deckCardsGrid) return;
    deckCardsGrid.replaceChildren();

    const cards = deckData.cards || {};
    let totalCards = 0;

    for (const [key, label] of PILE_LABELS) {
      const pile = cards[key];
      if (!pile || !Array.isArray(pile) || pile.length === 0) continue;

      const pileDiv = document.createElement("div");
      pileDiv.className = "deck-pile";

      const h3 = document.createElement("h3");
      h3.textContent = `${label} (${pile.length})`;
      pileDiv.appendChild(h3);

      const ul = document.createElement("ul");
      for (const card of pile) {
        const li = document.createElement("li");
        const nameSpan = document.createElement("span");
        nameSpan.className = "card-name";
        nameSpan.textContent = card.name || `ID:${card.cardId || "?"}`;
        const qtySpan = document.createElement("span");
        qtySpan.className = "qty";
        qtySpan.textContent = `${card.count || 1}x`;
        li.appendChild(nameSpan);
        li.appendChild(qtySpan);
        ul.appendChild(li);
        totalCards += (card.count || 1);
      }
      pileDiv.appendChild(ul);
      deckCardsGrid.appendChild(pileDiv);
    }

    if (deckCardsGrid.children.length === 0) {
      const p = document.createElement("p");
      p.className = "meta";
      p.textContent = "Keine Karten in diesem Deck.";
      deckCardsGrid.appendChild(p);
    }

    if (deckDetailMeta) {
      const source = deckData.source || "unknown";
      const deckId = deckData.deckId || "?";
      deckDetailMeta.textContent = `Quelle: ${source} · Deck-ID: ${deckId} · ${totalCards} Karten`;
    }
  };

  const loadDeckDetail = async (deckId, deckName) => {
    if (deckDetail) deckDetail.classList.add("active");
    if (deckDetailName) deckDetailName.textContent = deckName || "Unnamed";
    if (deckCardsGrid) {
      deckCardsGrid.replaceChildren();
      const loadingP = document.createElement("p");
      loadingP.className = "meta";
      loadingP.textContent = "Lade Karten...";
      deckCardsGrid.appendChild(loadingP);
    }

    try {
      const resp = await fetch(`/api/deck/${encodeURIComponent(deckId)}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        if (deckCardsGrid) {
          deckCardsGrid.replaceChildren();
          const p = document.createElement("p");
          p.className = "meta";
          p.textContent = `Keine Karten verfügbar (${err.message || resp.status})`;
          deckCardsGrid.appendChild(p);
        }
        return;
      }
      const data = await resp.json();
      renderDeckCards(data);
    } catch (err) {
      if (deckCardsGrid) {
        deckCardsGrid.replaceChildren();
        const p = document.createElement("p");
        p.className = "meta";
        p.textContent = `Karten konnten nicht geladen werden: ${err.message}`;
        deckCardsGrid.appendChild(p);
      }
    }
  };

  const openChatForDeck = (deckId, deckName) => {
    selectedDeck = {
      deckId,
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

    // Load deck cards into chat panel header
    if (chatDeckCards) {
      chatDeckCards.replaceChildren();
      const loadingSpan = document.createElement("span");
      loadingSpan.className = "meta";
      loadingSpan.textContent = "Lade Karten...";
      chatDeckCards.appendChild(loadingSpan);

      fetch(`/api/deck/${encodeURIComponent(deckId)}`)
        .then((r) => r.json())
        .then((data) => {
          chatDeckCards.replaceChildren();
          const cards = data.cards || {};
          for (const [key, label] of PILE_LABELS) {
            const pile = cards[key];
            if (!pile || !Array.isArray(pile) || pile.length === 0) continue;
            const pileSpan = document.createElement("div");
            pileSpan.className = "chat-deck-pile";
            const entries = pile.slice(0, 30).map(
              (c) => `${c.count || 1}x ${c.name || "ID:" + (c.cardId || "?")}`
            );
            pileSpan.textContent = `${label}: ${entries.join(", ")}`;
            if (pile.length > 30) {
              pileSpan.textContent += ` (+${pile.length - 30})`;
            }
            chatDeckCards.appendChild(pileSpan);
          }
          if (chatDeckCards.children.length === 0) {
            const p = document.createElement("span");
            p.className = "meta";
            p.textContent = "Keine Karten verfügbar.";
            chatDeckCards.appendChild(p);
          }
        })
        .catch(() => {
          chatDeckCards.replaceChildren();
          const p = document.createElement("span");
          p.className = "meta";
          p.textContent = "Karten konnten nicht geladen werden.";
          chatDeckCards.appendChild(p);
        });
    }

    addMessage("assistant", `Deck "${deckName || "Unnamed"}" geladen. Frag mich was!`);
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
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const deckId = button.dataset.deckId || "";
      const deckName = button.dataset.deckName || "Unnamed";
      loadDeckDetail(deckId, deckName);
      openChatForDeck(deckId, deckName);
    });
  });

  // Also handle deck-row clicks (the table rows)
  document.querySelectorAll(".deck-row").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest(".btn-select-deck")) return;
      const btn = row.querySelector(".btn-select-deck");
      if (btn) {
        const deckId = btn.dataset.deckId || "";
        const deckName = btn.dataset.deckName || "Unnamed";
        loadDeckDetail(deckId, deckName);
        openChatForDeck(deckId, deckName);
      }
    });
  });

  if (deckDetailClose) {
    deckDetailClose.addEventListener("click", () => {
      if (deckDetail) deckDetail.classList.remove("active");
    });
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
          deck_id: selectedDeck.deckId,
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
});
