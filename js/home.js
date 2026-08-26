const sections = {
    home: document.querySelector("#landing-wrapper"),
    experience: document.querySelector("#experience-wrapper"),
    contact: document.querySelector("#contact-wrapper"),
};

const navLinks = {
    home: document.querySelector("#nav_home"),
    experience: document.querySelector("#nav_about"),
    contact: document.querySelector("#nav_contact"),
};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const systemDarkTheme = window.matchMedia("(prefers-color-scheme: dark)");
const themeToggle = document.querySelector("#theme-toggle");
const themeColorMeta = document.querySelector("#theme-color-meta");

function savedTheme() {
    try {
        const theme = localStorage.getItem("theme");
        return theme === "light" || theme === "dark" ? theme : null;
    } catch (error) {
        return null;
    }
}

function applyTheme(theme, persist = false) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    themeColorMeta.setAttribute("content", theme === "dark" ? "#111719" : "#f7f7f7");

    const darkModeEnabled = theme === "dark";
    const nextTheme = darkModeEnabled ? "light" : "dark";
    const toggleLabel = `Switch to ${nextTheme} mode`;
    themeToggle.setAttribute("aria-pressed", String(darkModeEnabled));
    themeToggle.setAttribute("aria-label", toggleLabel);
    themeToggle.setAttribute("title", toggleLabel);

    if (persist) {
        try {
            localStorage.setItem("theme", theme);
        } catch (error) {
            // The selected theme still applies when storage is unavailable.
        }
    }
}

themeToggle.addEventListener("click", () => {
    const currentTheme = document.documentElement.getAttribute("data-bs-theme");
    applyTheme(currentTheme === "dark" ? "light" : "dark", true);
});

systemDarkTheme.addEventListener("change", (event) => {
    if (!savedTheme()) {
        applyTheme(event.matches ? "dark" : "light");
    }
});

applyTheme(document.documentElement.getAttribute("data-bs-theme") || "light");
requestAnimationFrame(() => requestAnimationFrame(() => {
    document.documentElement.classList.add("theme-transitions");
}));

function showSection(sectionName, animate = true, duration = 200) {
    const selectedSection = sections[sectionName] || sections.home;
    const selectedName = sections[sectionName] ? sectionName : "home";

    Object.values(sections).forEach((section) => {
        section.hidden = section !== selectedSection;
    });

    Object.entries(navLinks).forEach(([name, link]) => {
        const isCurrent = name === selectedName;
        link.classList.toggle("border-bottom", isCurrent);
        if (isCurrent) {
            link.setAttribute("aria-current", "page");
        } else {
            link.removeAttribute("aria-current");
        }
    });

    if (animate && !reducedMotion.matches) {
        selectedSection.animate(
            [{ opacity: 0 }, { opacity: 1 }],
            { duration, easing: "ease-out" },
        );
    }
}

function sectionFromHash() {
    return window.location.hash.slice(1) || "home";
}

document.querySelectorAll("[data-section-link]").forEach((link) => {
    link.addEventListener("click", (event) => {
        event.preventDefault();
        const sectionName = link.dataset.sectionLink;
        history.pushState(null, "", `#${sectionName}`);
        showSection(sectionName);

        const navigation = document.querySelector("#primary-navigation");
        if (navigation.classList.contains("show")) {
            bootstrap.Collapse.getOrCreateInstance(navigation).hide();
        }
    });
});

window.addEventListener("popstate", () => showSection(sectionFromHash(), false));
const initialSection = sectionFromHash();
showSection(initialSection, initialSection === "home", 2000);
