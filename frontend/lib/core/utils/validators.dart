class Validators {
  static final RegExp _email = RegExp(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$');

  static final RegExp _website = RegExp(
    r'^(https?://)?([\p{L}\p{N}]([\p{L}\p{N}-]*[\p{L}\p{N}])?\.)+\p{L}{2,}(:\d{1,5})?([/?#]\S*)?$',
    caseSensitive: false,
    unicode: true,
  );

  static const String invalidEmail = 'Podaj poprawny adres e-mail';
  static const String invalidWebsite = 'Podaj poprawny link do strony, np. https://sklep.pl';

  static bool isEmail(String value) => _email.hasMatch(value.trim());

  static bool isWebsiteUrl(String value) => _website.hasMatch(value.trim());
}
