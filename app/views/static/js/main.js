/**
 * Attendly Main Frontend Interactions
 */
document.addEventListener("DOMContentLoaded", () => {
    // Password show/hide toggle on Login screen
    const toggleBtn = document.getElementById("togglePasswordBtn");
    const passwordInput = document.getElementById("password");
    const eyeOpen = document.getElementById("eyeOpenIcon");
    const eyeClosed = document.getElementById("eyeClosedIcon");

    if (toggleBtn && passwordInput) {
        toggleBtn.addEventListener("click", () => {
            const isHidden = passwordInput.getAttribute("type") === "password";
            passwordInput.setAttribute("type", isHidden ? "text" : "password");
            if (eyeOpen && eyeClosed) {
                eyeOpen.style.display = isHidden ? "none" : "block";
                eyeClosed.style.display = isHidden ? "block" : "none";
            }
        });
    }
});

/**
 * Helper to autofill demo accounts on the login page
 */
function fillDemoAccount(username, password) {
    const userField = document.getElementById("username");
    const passField = document.getElementById("password");
    if (userField && passField) {
        userField.value = username;
        passField.value = password;
        userField.focus();
    }
}
