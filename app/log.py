from colorama import Fore

class Log:
    @staticmethod
    def info(message):
        print(f"{Fore.BLUE}[INFO] {message}{Fore.RESET}")

    @staticmethod
    def debug(message):
        print(f"{Fore.WHITE}[DEBUG] {message}{Fore.RESET}")

    @staticmethod
    def warning(message):
        print(f"{Fore.YELLOW}[WARN] {message}{Fore.RESET}")

    @staticmethod
    def error(message):
        print(f"{Fore.RED}[ERROR] {message}{Fore.RESET}")

    @staticmethod
    def success(message):
        print(f"{Fore.GREEN}[OK] {message}{Fore.RESET}")