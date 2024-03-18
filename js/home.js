function showLandingDiv(initial=false) {

    $("#nav_about").removeClass("border-bottom")
    $("#nav_contact").removeClass("border-bottom")
    $("#nav_home").addClass("border-bottom")

    var fadeSpeed = (initial === true) ? 2000 : "fast";
    $("#experience-wrapper").hide()
    $("#contact-wrapper").hide()

    $("#landing-wrapper").fadeIn(fadeSpeed, "swing")
}

function showExperienceDiv() {
    $("#nav_home").removeClass("border-bottom")
    $("#nav_contact").removeClass("border-bottom")
    $("#nav_about").addClass("border-bottom")

    $("#landing-wrapper").hide()
    $("#contact-wrapper").hide()
    $("#experience-wrapper").fadeIn("fast")
}

function showContactDiv() {
    $("#nav_home").removeClass("border-bottom")
    $("#nav_about").removeClass("border-bottom")
    $("#nav_contact").addClass("border-bottom")


    $("#landing-wrapper").hide()
    $("#experience-wrapper").hide()
    $("#contact-wrapper").fadeIn("fast")
}


jQuery(document).ready(function() {
    showLandingDiv(true);
});
