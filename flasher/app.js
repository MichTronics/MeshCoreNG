const select = document.getElementById("board-select");
const description = document.getElementById("board-description");
const installButton = document.getElementById("install-button");

async function loadBoards() {
  const response = await fetch("./boards.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load boards.json: ${response.status}`);
  }
  return response.json();
}

function selectBoard(board) {
  description.textContent = board.description || "";
  installButton.setAttribute("manifest", board.manifest);
}

loadBoards()
  .then((boards) => {
    boards.forEach((board, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = board.name;
      select.appendChild(option);
    });

    select.addEventListener("change", () => {
      selectBoard(boards[Number(select.value)]);
    });

    if (boards.length > 0) {
      select.value = "0";
      selectBoard(boards[0]);
    }
  })
  .catch((error) => {
    description.textContent = error.message;
  });
