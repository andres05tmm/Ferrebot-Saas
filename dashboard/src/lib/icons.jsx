/*
 * Iconografía del dashboard — Phosphor detrás de los nombres de siempre.
 *
 * Lucide es el set por defecto de cualquier generador: se reconoce a un kilómetro. Se cambió a
 * Phosphor, que tiene cara propia. Para no reescribir 100 archivos con nombres nuevos, este módulo
 * traduce: el código sigue pidiendo `Truck` o `AlertTriangle` y aquí se resuelve al componente
 * Phosphor equivalente.
 *
 * Cada icono sale envuelto con `weight="regular"` para que ninguno se vea más gordo que el de al
 * lado, y con el tamaño gobernado por la clase de siempre (`size-4`, `size-5`…): Phosphor dibuja a
 * 1em y la utilidad de Tailwind manda sobre eso.
 * Para añadir uno: impórtalo de Phosphor abajo y expórtalo con el nombre que use el código.
 */
import { forwardRef } from 'react'
import {
  Armchair as PhArmchair,
  ArrowCircleDown as PhArrowCircleDown,
  ArrowCircleUp as PhArrowCircleUp,
  ArrowClockwise as PhArrowClockwise,
  ArrowDownLeft as PhArrowDownLeft,
  ArrowLeft as PhArrowLeft,
  ArrowLineDown as PhArrowLineDown,
  ArrowLineUp as PhArrowLineUp,
  ArrowRight as PhArrowRight,
  ArrowSquareOut as PhArrowSquareOut,
  ArrowUUpLeft as PhArrowUUpLeft,
  ArrowUpRight as PhArrowUpRight,
  ArrowsClockwise as PhArrowsClockwise,
  ArrowsLeftRight as PhArrowsLeftRight,
  Bank as PhBank,
  Bed as PhBed,
  BellRinging as PhBellRinging,
  BellSlash as PhBellSlash,
  Bicycle as PhBicycle,
  BookOpen as PhBookOpen,
  BookOpenText as PhBookOpenText,
  Books as PhBooks,
  Briefcase as PhBriefcase,
  Broadcast as PhBroadcast,
  Buildings as PhBuildings,
  Calculator as PhCalculator,
  CalendarBlank as PhCalendarBlank,
  CalendarDots as PhCalendarDots,
  CalendarPlus as PhCalendarPlus,
  Camera as PhCamera,
  CaretDown as PhCaretDown,
  CaretLeft as PhCaretLeft,
  CaretRight as PhCaretRight,
  CaretUp as PhCaretUp,
  ChartBar as PhChartBar,
  ChatCircle as PhChatCircle,
  ChatTeardropText as PhChatTeardropText,
  Check as PhCheck,
  CheckCircle as PhCheckCircle,
  ChefHat as PhChefHat,
  Circle as PhCircle,
  ClipboardText as PhClipboardText,
  Clock as PhClock,
  ClockCounterClockwise as PhClockCounterClockwise,
  Coins as PhCoins,
  ColumnsPlusRight as PhColumnsPlusRight,
  Command as PhCommand,
  CreditCard as PhCreditCard,
  CubeFocus as PhCubeFocus,
  CurrencyDollar as PhCurrencyDollar,
  Disc as PhDisc,
  DownloadSimple as PhDownloadSimple,
  Drop as PhDrop,
  DropSimple as PhDropSimple,
  Envelope as PhEnvelope,
  FileCode as PhFileCode,
  FileText as PhFileText,
  Fire as PhFire,
  Flask as PhFlask,
  Gauge as PhGauge,
  Gear as PhGear,
  GridFour as PhGridFour,
  Hammer as PhHammer,
  HandCoins as PhHandCoins,
  HardHat as PhHardHat,
  Hash as PhHash,
  Headset as PhHeadset,
  Hexagon as PhHexagon,
  House as PhHouse,
  ImageSquare as PhImageSquare,
  Key as PhKey,
  Lightning as PhLightning,
  Link as PhLink,
  LinkSimple as PhLinkSimple,
  Lock as PhLock,
  LockOpen as PhLockOpen,
  MagicWand as PhMagicWand,
  MagnifyingGlass as PhMagnifyingGlass,
  MapPin as PhMapPin,
  Minus as PhMinus,
  Money as PhMoney,
  Monitor as PhMonitor,
  Moon as PhMoon,
  Package as PhPackage,
  PaintBrush as PhPaintBrush,
  Palette as PhPalette,
  PaperPlaneTilt as PhPaperPlaneTilt,
  Path as PhPath,
  PencilSimple as PhPencilSimple,
  Percent as PhPercent,
  Phone as PhPhone,
  Play as PhPlay,
  PlayCircle as PhPlayCircle,
  Plus as PhPlus,
  Power as PhPower,
  Pulse as PhPulse,
  PushPin as PhPushPin,
  QrCode as PhQrCode,
  Receipt as PhReceipt,
  Robot as PhRobot,
  Ruler as PhRuler,
  Scales as PhScales,
  Scan as PhScan,
  SealCheck as PhSealCheck,
  ShieldCheck as PhShieldCheck,
  ShoppingCart as PhShoppingCart,
  Sidebar as PhSidebar,
  SidebarSimple as PhSidebarSimple,
  SignIn as PhSignIn,
  SignOut as PhSignOut,
  SlidersHorizontal as PhSlidersHorizontal,
  SortAscending as PhSortAscending,
  SpinnerGap as PhSpinnerGap,
  SprayBottle as PhSprayBottle,
  Square as PhSquare,
  SquaresFour as PhSquaresFour,
  Stack as PhStack,
  Star as PhStar,
  Sun as PhSun,
  Tag as PhTag,
  Timer as PhTimer,
  Trash as PhTrash,
  Tray as PhTray,
  TreeStructure as PhTreeStructure,
  TrendDown as PhTrendDown,
  TrendUp as PhTrendUp,
  Trophy as PhTrophy,
  Truck as PhTruck,
  User as PhUser,
  UserCheck as PhUserCheck,
  UserCircle as PhUserCircle,
  UserPlus as PhUserPlus,
  Users as PhUsers,
  Wallet as PhWallet,
  Warning as PhWarning,
  WarningCircle as PhWarningCircle,
  Wrench as PhWrench,
  X as PhX,
  XCircle as PhXCircle,
} from '@phosphor-icons/react'

/** Envuelve un icono de Phosphor: trazo uniforme y tamaño gobernado por CSS (size-4, size-5…). */
function envolver(Icono, nombre) {
  const C = forwardRef(function Icono_(props, ref) {
    return <Icono ref={ref} weight="regular" {...props} />
  })
  C.displayName = nombre
  return C
}

export const Activity = envolver(PhPulse, 'Activity')
export const AlertCircle = envolver(PhWarningCircle, 'AlertCircle')
export const AlertTriangle = envolver(PhWarning, 'AlertTriangle')
export const Armchair = envolver(PhArmchair, 'Armchair')
export const ArrowDownCircle = envolver(PhArrowCircleDown, 'ArrowDownCircle')
export const ArrowDownLeft = envolver(PhArrowDownLeft, 'ArrowDownLeft')
export const ArrowDownToLine = envolver(PhArrowLineDown, 'ArrowDownToLine')
export const ArrowLeft = envolver(PhArrowLeft, 'ArrowLeft')
export const ArrowRight = envolver(PhArrowRight, 'ArrowRight')
export const ArrowRightLeft = envolver(PhArrowsLeftRight, 'ArrowRightLeft')
export const ArrowUpCircle = envolver(PhArrowCircleUp, 'ArrowUpCircle')
export const ArrowUpFromLine = envolver(PhArrowLineUp, 'ArrowUpFromLine')
export const ArrowUpNarrowWide = envolver(PhSortAscending, 'ArrowUpNarrowWide')
export const ArrowUpRight = envolver(PhArrowUpRight, 'ArrowUpRight')
export const Banknote = envolver(PhMoney, 'Banknote')
export const BarChart3 = envolver(PhChartBar, 'BarChart3')
export const BedDouble = envolver(PhBed, 'BedDouble')
export const BellOff = envolver(PhBellSlash, 'BellOff')
export const BellRing = envolver(PhBellRinging, 'BellRing')
export const Bike = envolver(PhBicycle, 'Bike')
export const Blocks = envolver(PhSquaresFour, 'Blocks')
export const BookOpen = envolver(PhBookOpen, 'BookOpen')
export const BookText = envolver(PhBookOpenText, 'BookText')
export const Bot = envolver(PhRobot, 'Bot')
export const Boxes = envolver(PhStack, 'Boxes')
export const Briefcase = envolver(PhBriefcase, 'Briefcase')
export const Brush = envolver(PhPaintBrush, 'Brush')
export const Building2 = envolver(PhBuildings, 'Building2')
export const Calculator = envolver(PhCalculator, 'Calculator')
export const CalendarClock = envolver(PhCalendarDots, 'CalendarClock')
export const CalendarDays = envolver(PhCalendarBlank, 'CalendarDays')
export const CalendarPlus = envolver(PhCalendarPlus, 'CalendarPlus')
export const Camera = envolver(PhCamera, 'Camera')
export const ChartColumn = envolver(PhChartBar, 'ChartColumn')
export const Check = envolver(PhCheck, 'Check')
export const CheckCircle2 = envolver(PhCheckCircle, 'CheckCircle2')
export const ChefHat = envolver(PhChefHat, 'ChefHat')
export const ChevronDown = envolver(PhCaretDown, 'ChevronDown')
export const ChevronLeft = envolver(PhCaretLeft, 'ChevronLeft')
export const ChevronRight = envolver(PhCaretRight, 'ChevronRight')
export const ChevronUp = envolver(PhCaretUp, 'ChevronUp')
export const Circle = envolver(PhCircle, 'Circle')
export const ClipboardList = envolver(PhClipboardText, 'ClipboardList')
export const Clock = envolver(PhClock, 'Clock')
export const Cog = envolver(PhGear, 'Cog')
export const Coins = envolver(PhCoins, 'Coins')
export const Command = envolver(PhCommand, 'Command')
export const CreditCard = envolver(PhCreditCard, 'CreditCard')
export const Disc = envolver(PhDisc, 'Disc')
export const DollarSign = envolver(PhCurrencyDollar, 'DollarSign')
export const Download = envolver(PhDownloadSimple, 'Download')
export const Droplet = envolver(PhDrop, 'Droplet')
export const Droplets = envolver(PhDropSimple, 'Droplets')
export const ExternalLink = envolver(PhArrowSquareOut, 'ExternalLink')
export const FileCheck = envolver(PhSealCheck, 'FileCheck')
export const FileCog = envolver(PhFileCode, 'FileCog')
export const FileText = envolver(PhFileText, 'FileText')
export const Flame = envolver(PhFire, 'Flame')
export const FlaskConical = envolver(PhFlask, 'FlaskConical')
export const Gauge = envolver(PhGauge, 'Gauge')
export const Hammer = envolver(PhHammer, 'Hammer')
export const HandCoins = envolver(PhHandCoins, 'HandCoins')
export const HardHat = envolver(PhHardHat, 'HardHat')
export const Hash = envolver(PhHash, 'Hash')
export const Headset = envolver(PhHeadset, 'Headset')
export const Hexagon = envolver(PhHexagon, 'Hexagon')
export const History = envolver(PhClockCounterClockwise, 'History')
export const Home = envolver(PhHouse, 'Home')
export const ImagePlus = envolver(PhImageSquare, 'ImagePlus')
export const Inbox = envolver(PhTray, 'Inbox')
export const KeyRound = envolver(PhKey, 'KeyRound')
export const Landmark = envolver(PhBank, 'Landmark')
export const LayoutDashboard = envolver(PhSquaresFour, 'LayoutDashboard')
export const LayoutGrid = envolver(PhGridFour, 'LayoutGrid')
export const Library = envolver(PhBooks, 'Library')
export const Link = envolver(PhLink, 'Link')
export const Link2 = envolver(PhLinkSimple, 'Link2')
export const ListTree = envolver(PhTreeStructure, 'ListTree')
export const Loader2 = envolver(PhSpinnerGap, 'Loader2')
export const Lock = envolver(PhLock, 'Lock')
export const LockOpen = envolver(PhLockOpen, 'LockOpen')
export const LogIn = envolver(PhSignIn, 'LogIn')
export const LogOut = envolver(PhSignOut, 'LogOut')
export const Mail = envolver(PhEnvelope, 'Mail')
export const MapPin = envolver(PhMapPin, 'MapPin')
export const MessageCircle = envolver(PhChatCircle, 'MessageCircle')
export const MessageSquareHeart = envolver(PhChatTeardropText, 'MessageSquareHeart')
export const Minus = envolver(PhMinus, 'Minus')
export const Monitor = envolver(PhMonitor, 'Monitor')
export const Moon = envolver(PhMoon, 'Moon')
export const Package = envolver(PhPackage, 'Package')
export const PackageSearch = envolver(PhCubeFocus, 'PackageSearch')
export const Paintbrush = envolver(PhPaintBrush, 'Paintbrush')
export const Palette = envolver(PhPalette, 'Palette')
export const PanelLeftClose = envolver(PhSidebarSimple, 'PanelLeftClose')
export const PanelLeftOpen = envolver(PhSidebar, 'PanelLeftOpen')
export const Pencil = envolver(PhPencilSimple, 'Pencil')
export const Percent = envolver(PhPercent, 'Percent')
export const Phone = envolver(PhPhone, 'Phone')
export const Pin = envolver(PhPushPin, 'Pin')
export const Play = envolver(PhPlay, 'Play')
export const PlayCircle = envolver(PhPlayCircle, 'PlayCircle')
export const Plus = envolver(PhPlus, 'Plus')
export const Power = envolver(PhPower, 'Power')
export const QrCode = envolver(PhQrCode, 'QrCode')
export const Radar = envolver(PhBroadcast, 'Radar')
export const Receipt = envolver(PhReceipt, 'Receipt')
export const ReceiptText = envolver(PhReceipt, 'ReceiptText')
export const RefreshCw = envolver(PhArrowsClockwise, 'RefreshCw')
export const RotateCw = envolver(PhArrowClockwise, 'RotateCw')
export const Route = envolver(PhPath, 'Route')
export const Ruler = envolver(PhRuler, 'Ruler')
export const Scale = envolver(PhScales, 'Scale')
export const ScanLine = envolver(PhScan, 'ScanLine')
export const Search = envolver(PhMagnifyingGlass, 'Search')
export const Send = envolver(PhPaperPlaneTilt, 'Send')
export const Settings = envolver(PhGear, 'Settings')
export const ShieldCheck = envolver(PhShieldCheck, 'ShieldCheck')
export const ShoppingCart = envolver(PhShoppingCart, 'ShoppingCart')
export const SlidersHorizontal = envolver(PhSlidersHorizontal, 'SlidersHorizontal')
export const SplitSquareHorizontal = envolver(PhColumnsPlusRight, 'SplitSquareHorizontal')
export const SprayCan = envolver(PhSprayBottle, 'SprayCan')
export const Square = envolver(PhSquare, 'Square')
export const Star = envolver(PhStar, 'Star')
export const Sun = envolver(PhSun, 'Sun')
export const Tag = envolver(PhTag, 'Tag')
export const Timer = envolver(PhTimer, 'Timer')
export const Trash2 = envolver(PhTrash, 'Trash2')
export const TrendingDown = envolver(PhTrendDown, 'TrendingDown')
export const TrendingUp = envolver(PhTrendUp, 'TrendingUp')
export const TriangleAlert = envolver(PhWarning, 'TriangleAlert')
export const Trophy = envolver(PhTrophy, 'Trophy')
export const Truck = envolver(PhTruck, 'Truck')
export const Undo2 = envolver(PhArrowUUpLeft, 'Undo2')
export const User = envolver(PhUser, 'User')
export const UserCheck = envolver(PhUserCheck, 'UserCheck')
export const UserPlus = envolver(PhUserPlus, 'UserPlus')
export const UserRound = envolver(PhUserCircle, 'UserRound')
export const Users = envolver(PhUsers, 'Users')
export const Wallet = envolver(PhWallet, 'Wallet')
export const Wand = envolver(PhMagicWand, 'Wand')
export const Wand2 = envolver(PhMagicWand, 'Wand2')
export const Wrench = envolver(PhWrench, 'Wrench')
export const X = envolver(PhX, 'X')
export const XCircle = envolver(PhXCircle, 'XCircle')
export const Zap = envolver(PhLightning, 'Zap')
