// main.js — students will add JavaScript here as features are built

document.addEventListener("submit", function (event) {
    const form = event.target;
    if (form.matches("[data-confirm-delete]")) {
        if (!confirm("Delete this expense?")) {
            event.preventDefault();
        }
    }
});
