const page = document.getElementById("page");
wrapTables(page);

function wrapTables(root) {
  for (const table of root.querySelectorAll("table")) {
    const box = document.createElement("div");
    box.className = "table";
    table.before(box);
    box.append(table);
  }
}
