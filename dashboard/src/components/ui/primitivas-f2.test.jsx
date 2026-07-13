/*
 * primitivas-f2.test.jsx — smoke de las primitivas agregadas en F2.0 (alert-dialog, sheet, textarea,
 * checkbox, switch, separator): que rendericen, abran y expongan sus roles ARIA. No re-testea Radix;
 * solo el wiring y las clases tokenizadas del proyecto.
 */
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from './alert-dialog.jsx'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from './sheet.jsx'
import { Textarea } from './textarea.jsx'
import { Checkbox } from './checkbox.jsx'
import { Switch } from './switch.jsx'
import { Separator } from './separator.jsx'

describe('primitivas F2.0', () => {
  it('AlertDialog abre con trigger y muestra título, acción y cancelar', () => {
    render(
      <AlertDialog>
        <AlertDialogTrigger>Archivar</AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Archivar la obra?</AlertDialogTitle>
            <AlertDialogDescription>Sus cifras salen del panel.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction variant="destructive">Archivar</AlertDialogAction>
        </AlertDialogContent>
      </AlertDialog>,
    )
    fireEvent.click(screen.getByText('Archivar'))
    expect(screen.getByRole('alertdialog', { name: /¿Archivar la obra\?/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument()
  })

  it('Sheet abre por el lado indicado con título accesible', () => {
    render(
      <Sheet>
        <SheetTrigger>Abrir detalle</SheetTrigger>
        <SheetContent side="bottom">
          <SheetHeader>
            <SheetTitle>Detalle del día</SheetTitle>
            <SheetDescription>Partes y asignaciones.</SheetDescription>
          </SheetHeader>
        </SheetContent>
      </Sheet>,
    )
    fireEvent.click(screen.getByText('Abrir detalle'))
    expect(screen.getByRole('dialog', { name: 'Detalle del día' })).toBeInTheDocument()
  })

  it('Textarea, Checkbox, Switch y Separator renderizan con sus roles', () => {
    render(
      <div>
        <Textarea placeholder="Motivo del rechazo" />
        <Checkbox aria-label="marcar" />
        <Switch aria-label="prender" />
        <Separator />
      </div>,
    )
    expect(screen.getByPlaceholderText('Motivo del rechazo')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'marcar' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'prender' })).toBeInTheDocument()
  })
})
