const form = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question");
const conversation = document.querySelector("#conversation");
const submitButton = document.querySelector("#submit");
const imageInput = document.querySelector("#image");
const attachmentName = document.querySelector("#attachment-name");
const clearImageButton = document.querySelector("#clear-image");

function addMessage(content, role, sources = [], question = "") {
  const message = document.createElement("article");
  message.className = `message ${role}`;
  message.textContent = content;

  if (sources.length) {
    const sourceList = document.createElement("ul");
    sourceList.className = "sources";
    for (const source of sources) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = source.document_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = `${source.lecture}, pagina ${source.page}`;
      link.setAttribute("aria-label", `Apri la dispensa ${source.lecture}, pagina ${source.page}`);
      item.append(link);
      sourceList.append(item);
    }
    message.append(sourceList);
  }

  if (role === "assistant" && question) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const illustrationButton = document.createElement("button");
    illustrationButton.type = "button";
    illustrationButton.className = "illustration-button";
    illustrationButton.textContent = "Genera immagine";
    illustrationButton.addEventListener("click", async () => {
      illustrationButton.disabled = true;
      illustrationButton.textContent = "Generazione";
      try {
        const response = await fetch("/api/illustrations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, answer: content }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Errore durante la generazione.");
        const image = document.createElement("img");
        image.className = "generated-image";
        image.src = data.image_url;
        image.alt = "Illustrazione generata della risposta";
        message.append(image);
        actions.remove();
      } catch (error) {
        illustrationButton.disabled = false;
        illustrationButton.textContent = "Riprova immagine";
      }
    });
    actions.append(illustrationButton);
    message.append(actions);
  }

  conversation.append(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
}

function clearAttachment() {
  imageInput.value = "";
  attachmentName.textContent = "";
  clearImageButton.hidden = true;
}

imageInput.addEventListener("change", () => {
  const [file] = imageInput.files;
  attachmentName.textContent = file ? file.name : "";
  clearImageButton.hidden = !file;
});

clearImageButton.addEventListener("click", clearAttachment);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  const formData = new FormData();
  formData.append("question", question);
  if (imageInput.files[0]) formData.append("image", imageInput.files[0]);

  addMessage(`${question}${imageInput.files[0] ? "\n[Immagine allegata]" : ""}`, "user");
  questionInput.value = "";
  clearAttachment();
  submitButton.disabled = true;
  submitButton.textContent = "Attendi";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Errore durante la richiesta.");
    addMessage(data.answer, "assistant", data.sources, question);
  } catch (error) {
    addMessage(error.message, "error");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Invia";
    questionInput.focus();
  }
});