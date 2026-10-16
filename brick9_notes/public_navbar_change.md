In templates/includes/public_navbar.html wrap the toggle button so only visitors who are not signed in get it (rule 9.3):

    {% if not user.is_authenticated %}
    <button type="button"
            class="btn btn-ghost !p-1.5"
            :aria-label="dark ? 'Switch to light mode' : 'Switch to dark mode'"
            @click="dark = toggleMode() === 'dark'">
      ... (the two icon spans stay as they are)
    </button>
    {% endif %}

A signed-in person looking at /give/ or the landing page already sees "Open app", and their saved choice
wins over localStorage, so a toggle there would flip and then snap back on the next load.
