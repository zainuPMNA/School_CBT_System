/* Live KaTeX preview for the question editor (Alpine.js component).
 * Text is inserted with textContent (never innerHTML), then KaTeX renders it,
 * so authored content cannot inject HTML. */
function questionPreview() {
  "use strict";
  var FIELDS = {
    q: "id_question_text",
    a: "id_option_a",
    b: "id_option_b",
    c: "id_option_c",
    d: "id_option_d",
    e: "id_explanation"
  };
  return {
    correct: "",
    imageUrl: "",
    init: function () {
      var self = this;
      var existing = document.getElementById("existing-image-url");
      if (existing) this.imageUrl = existing.dataset.url || "";
      var fileInput = document.getElementById("id_question_image");
      if (fileInput) {
        fileInput.addEventListener("change", function () {
          var file = fileInput.files && fileInput.files[0];
          self.imageUrl = file ? URL.createObjectURL(file) : (existing ? existing.dataset.url : "");
        });
      }
      this.render();
    },
    render: function () {
      for (var ref in FIELDS) {
        var src = document.getElementById(FIELDS[ref]);
        var out = this.$refs[ref];
        if (!src || !out) continue;
        out.textContent = src.value;
        if (window.cbtRenderMath) window.cbtRenderMath(out);
      }
      var checked = document.querySelector('input[name="correct_option"]:checked');
      this.correct = checked ? checked.value : "";
    }
  };
}
