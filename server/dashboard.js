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
      openChatForDeck(button.dataset.deckId || "", button.dataset.deckName || "Unnamed");
    });
  });

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
