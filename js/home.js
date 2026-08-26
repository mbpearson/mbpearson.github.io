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

function showSection(sectionName, animate = true) {
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
            { duration: 200, easing: "ease-out" },
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
showSection(sectionFromHash(), false);
